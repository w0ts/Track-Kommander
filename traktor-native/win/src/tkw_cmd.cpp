// tkw_cmd.cpp — the 127.0.0.1 command channel (the Mac libtk's line protocol, port 8767) and the
// small HTTP POSTs to TK's local server.
//
// A connection carries one command: "verb args\n". Verbs registered with_body (Mac "list",
// "cards") also take the following lines up to EOF (the client half-closes). Handlers run on
// Traktor's main thread; the reply is one line. Built-in verbs: ping, pyexec, rtti, log.
#include <winsock2.h>
#include <ws2tcpip.h>

#include <condition_variable>
#include <map>
#include <memory>
#include <mutex>
#include <thread>

#include "tkw.h"

#pragma comment(lib, "ws2_32.lib")

namespace {

struct Verb {
  TkwCmd fn;
  bool with_body;
};
std::mutex g_verbs_mu;
std::map<std::string, Verb> g_verbs;

struct Pending {
  std::mutex mu;
  std::condition_variable cv;
  bool done = false;
  bool cancelled = false;  // the client gave up: a late job must not run the command any more
  std::string reply;
};

std::string run_on_main_and_wait(const Verb& v, const std::string& args, std::vector<std::string> body) {
  // Shared with the queued job: after a timeout the job may still run, and must not touch a
  // dead stack frame.
  auto st = std::make_shared<Pending>();
  TkwCmd fn = v.fn;
  tkw_on_main([st, fn, args, body = std::move(body)] {
    {
      std::lock_guard<std::mutex> lock(st->mu);
      if (st->cancelled) return;
    }
    std::string r;
    try {
      r = fn(args, body);
    } catch (...) {
      r = "ERR exception";
    }
    std::lock_guard<std::mutex> lock(st->mu);
    st->reply = r;
    st->done = true;
    st->cv.notify_one();
  });
  std::unique_lock<std::mutex> lock(st->mu);
  if (!st->cv.wait_for(lock, std::chrono::seconds(10), [&] { return st->done; })) {
    st->cancelled = true;
    return "ERR main thread did not answer in 10 s (cancelled)";
  }
  return st->reply;
}

// TK's own verbs are acknowledged at once and run on the main thread afterwards, as the Mac
// libtk does (cmd_reply then dispatch_async). Inside Traktor TK's Qt code runs ON Traktor's UI
// thread: waiting there for a handler that needs that same thread blocked Traktor until the
// client's timeout (first Windows run, 2026-11-25: "main thread did not answer in 10 s", the
// results pusher paused, Traktor lagging). Their errors go to the log instead of the reply.
// Diagnostic verbs (band, prop, ping, ...) still wait for their real answer.
bool ack_at_once(const std::string& verb) {
  static const char* const kVerbs[] = {"load",     "read", "loadsel", "readsel", "load_path", "read_path",
                                       "nativeui", "deck", "bandhide", "auto",   "list",      "cards"};
  for (const char* v : kVerbs)
    if (verb == v) return true;
  return false;
}

std::string run_on_main_later(const Verb& v, const std::string& verb, const std::string& args,
                              std::vector<std::string> body) {
  const std::string ack = v.with_body ? "OK " + std::to_string(body.size()) : std::string("OK");
  TkwCmd fn = v.fn;
  tkw_on_main([fn, verb, args, body = std::move(body)] {
    std::string r;
    try {
      r = fn(args, body);
    } catch (...) {
      r = "ERR exception";
    }
    if (r.rfind("ERR", 0) == 0) tklog("CMD %s (acknowledged earlier) failed: %s", verb.c_str(), r.c_str());
  });
  return ack;
}

void rstrip(std::string* s) {
  while (!s->empty() && (s->back() == '\n' || s->back() == '\r' || s->back() == ' ')) s->pop_back();
}

void handle_conn(SOCKET fd) {
  DWORD tv = 3000;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));
  std::string data;
  char buf[8192];
  bool eof = false;
  while (data.size() < (8u << 20)) {  // first line (and, for body verbs, everything to EOF)
    const int n = recv(fd, buf, sizeof(buf), 0);
    if (n <= 0) {
      eof = n == 0;
      break;
    }
    data.append(buf, (size_t)n);
    const size_t nl = data.find('\n');
    if (nl == std::string::npos) continue;
    const std::string verb = data.substr(0, data.find_first_of(" \r\n"));
    std::lock_guard<std::mutex> lock(g_verbs_mu);
    auto it = g_verbs.find(verb);
    if (it == g_verbs.end() || !it->second.with_body) break;  // one-line command: done
  }
  const size_t nl = data.find('\n');
  std::string line = data.substr(0, nl);
  rstrip(&line);
  const size_t sp = line.find(' ');
  const std::string verb = line.substr(0, sp), args = sp == std::string::npos ? "" : line.substr(sp + 1);
  Verb v;
  {
    std::lock_guard<std::mutex> lock(g_verbs_mu);
    auto it = g_verbs.find(verb);
    if (it != g_verbs.end()) v = it->second;
  }
  std::string reply;
  if (line.empty()) {
    reply = "ERR empty";
  } else if (!v.fn) {
    reply = "ERR unknown command " + verb;
  } else {
    std::vector<std::string> body;
    if (v.with_body && nl != std::string::npos) {
      size_t pos = nl + 1;
      while (pos < data.size()) {
        size_t e = data.find('\n', pos);
        if (e == std::string::npos) e = data.size();
        std::string l = data.substr(pos, e - pos);
        rstrip(&l);
        if (!l.empty()) body.push_back(l);
        pos = e + 1;
      }
      if (!eof) tklog("CMD %s: body read stopped before EOF (%zu lines)", verb.c_str(), body.size());
    }
    reply = ack_at_once(verb) ? run_on_main_later(v, verb, args, std::move(body))
                              : run_on_main_and_wait(v, args, std::move(body));
  }
  reply += "\n";
  send(fd, reply.c_str(), (int)reply.size(), 0);
  tklog("CMD '%s' -> %s", line.substr(0, 160).c_str(), reply.substr(0, 200).c_str());
}

void listener(int port) {
  SOCKET srv = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  BOOL excl = TRUE;  // no other process can bind the same port and take our clients
  if (srv != INVALID_SOCKET) setsockopt(srv, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char*)&excl, sizeof(excl));
  sockaddr_in addr = {};
  addr.sin_family = AF_INET;
  addr.sin_port = htons((u_short)port);
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);  // 127.0.0.1 only — never the network
  if (srv == INVALID_SOCKET || bind(srv, (sockaddr*)&addr, sizeof(addr)) != 0 || listen(srv, 16) != 0) {
    tklog("CMD: cannot listen on 127.0.0.1:%d (err %d) — command channel off", port, WSAGetLastError());
    return;
  }
  tklog("CMD: listening on 127.0.0.1:%d", port);
  while (!g_tkw_quitting) {
    SOCKET fd = accept(srv, nullptr, nullptr);
    if (fd == INVALID_SOCKET) {
      if (WSAGetLastError() == WSAEINTR) continue;
      break;
    }
    handle_conn(fd);
    closesocket(fd);
  }
  closesocket(srv);
}

std::string norm_path(std::string p) {
  for (char& c : p) c = c == '/' ? '\\' : (char)tolower((unsigned char)c);
  return p;
}

// Any local process can reach 127.0.0.1:8767: Python runs only from TK's own folders, never a
// file of someone else's choosing (no "..", no relative path).
bool pyexec_allowed(const std::string& path) {
  const std::string p = norm_path(path);
  if (p.size() < 3 || p[1] != ':' || p.find("..") != std::string::npos) return false;
  std::string embed = norm_path(tkw_env("TK_EMBED"));
  embed = embed.substr(0, embed.find_last_of('\\') + 1);
  const std::string repo = norm_path(tkw_env("TK_REPO"));
  for (const std::string& root : {embed, repo.empty() ? std::string() : repo + "\\"})
    if (!root.empty() && p.rfind(root, 0) == 0) return true;
  tklog("CMD: pyexec refused for '%s' (outside TK_REPO and TK_EMBED's folder)", path.c_str());
  return false;
}

void register_builtins() {
  tkw_command("ping", [](const std::string&, const std::vector<std::string>&) { return std::string("pong"); });
  tkw_command("pyexec", [](const std::string& a, const std::vector<std::string>&) {
    if (!pyexec_allowed(a)) return std::string("ERR pyexec runs only files under TK_REPO or TK_EMBED's folder");
    tkw_pyexec(a);
    return std::string("OK");
  });
  tkw_command("rtti", [](const std::string& a, const std::vector<std::string>&) {
    return tkw_rtti_name((const void*)(uintptr_t)strtoull(a.c_str(), nullptr, 0));
  });
  tkw_command("bench", [](const std::string&, const std::vector<std::string>&) { return tkw_bench(); });
  tkw_command("log", [](const std::string& a, const std::vector<std::string>&) {
    tklog("CMD log: %s", a.c_str());
    return std::string("OK");
  });
}

}  // namespace

void tkw_command(const char* verb, TkwCmd fn, bool with_body) {
  std::lock_guard<std::mutex> lock(g_verbs_mu);
  if (g_verbs.count(verb)) tklog("CMD: verb '%s' registered twice — the later one wins", verb);
  g_verbs[verb] = Verb{std::move(fn), with_body};
}

void tkw_cmd_start(int port) {
  register_builtins();
  WSADATA w;
  WSAStartup(MAKEWORD(2, 2), &w);
  std::thread(listener, port > 0 && port < 65536 ? port : 8767).detach();
}

// One worker sends the POSTs in order (a "deckLoad" must not overtake the "load" before it).
namespace {
std::mutex g_post_mu;
std::condition_variable g_post_cv;
std::vector<std::pair<std::string, std::string>> g_posts;
bool g_post_worker = false;

void send_post(const std::string& p, const std::string& b, int port);

void post_worker() {
  const int port = atoi(tkw_env("TK_SHOW_PORT", "12345").c_str());
  std::unique_lock<std::mutex> lock(g_post_mu);
  while (!g_tkw_quitting) {
    g_post_cv.wait(lock, [] { return !g_posts.empty() || g_tkw_quitting; });
    std::vector<std::pair<std::string, std::string>> batch;
    batch.swap(g_posts);
    lock.unlock();
    for (const auto& pb : batch) send_post(pb.first, pb.second, port);
    lock.lock();
  }
}
}  // namespace

void tkw_post_json(const char* path, const char* body) {
  std::lock_guard<std::mutex> lock(g_post_mu);
  if (g_posts.size() >= 256) {
    tklog("POST %s dropped: %zu waiting (TK not answering?)", path, g_posts.size());
    return;
  }
  g_posts.emplace_back(path, body ? body : "{}");
  if (!g_post_worker) {
    g_post_worker = true;
    std::thread(post_worker).detach();
  }
  g_post_cv.notify_one();
}

namespace {
void send_post(const std::string& p, const std::string& b, int port) {
  {
    const std::string req = "POST " + p + " HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n" +
                            "Content-Length: " + std::to_string(b.size()) + "\r\nConnection: close\r\n\r\n" + b;
    for (int attempt = 0; attempt < 5; attempt++) {
      SOCKET fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
      sockaddr_in addr = {};
      addr.sin_family = AF_INET;
      addr.sin_port = htons((u_short)port);
      addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
      if (fd != INVALID_SOCKET && connect(fd, (sockaddr*)&addr, sizeof(addr)) == 0) {
        send(fd, req.c_str(), (int)req.size(), 0);
        // Read TK's answer before closing. TK's server writes "200" and only then acts on the
        // post; closing at once made Windows reset the connection under that write, and the
        // action was dropped (first Windows run, 2026-11-25: the Kommander node's KOMMANDER
        // never reached TK; the same post by curl, which reads the reply, did). This thread
        // only sends posts: waiting here never holds Traktor's UI.
        shutdown(fd, SD_SEND);
        DWORD tv = 2000;
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));
        char sink[512];
        int got = 0;
        for (int n; (n = recv(fd, sink, sizeof(sink), 0)) > 0;) got += n;
        if (got == 0) tklog("POST %s: no answer from TK (err %d)", p.c_str(), WSAGetLastError());
        closesocket(fd);
        return;
      }
      if (fd != INVALID_SOCKET) closesocket(fd);
      Sleep(40);
    }
    tklog("POST %s: 127.0.0.1:%d not answering (TK not running?)", p.c_str(), port);
  }
}
}  // namespace
