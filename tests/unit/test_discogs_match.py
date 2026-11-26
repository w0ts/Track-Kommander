"""Deciding which Discogs release a track is on."""

from __future__ import annotations

from core.discogs_match import (
    Query,
    norm_artist,
    norm_title,
    original_year,
    parse_duration,
    rank_candidates,
    score_release,
    search_title,
)


def test_generic_mix_tags_and_featured_artists_are_ignored() -> None:
    assert norm_title("Come Again (Original Mix)") == "come again"
    assert norm_title("Dust [Extended Mix]") == "dust"
    assert norm_title("Song feat. Someone") == "song"
    assert search_title("Come Again (Original Mix)") == "Come Again"


def test_a_remix_keeps_its_name() -> None:
    assert norm_title("Diode (Oscar Mulero Remix)") == "diode oscar mulero remix"


def test_discogs_artist_numbers_and_stars_are_ignored() -> None:
    assert norm_artist("Swayzak (2)") == "swayzak"
    assert norm_artist("Nørbak*") == "norbak"
    assert norm_artist("The Black Dog") == "black dog"


def test_durations() -> None:
    assert parse_duration("5:32") == 332
    assert parse_duration("1:02:10") == 3730
    assert parse_duration("") is None
    assert parse_duration("5'32") is None


def _release(*tracks, artist="Aero"):
    return {
        "artists": [{"name": artist}],
        "tracklist": [{"type_": "track", **t} for t in tracks],
    }


def test_the_track_is_found_on_the_release() -> None:
    q = Query("Aero", "Come Again (Original Mix)", seconds=401)
    release = _release(
        {"position": "A1", "title": "Straight", "duration": "6:10"},
        {"position": "A2", "title": "Come Again", "duration": "6:43"},
    )
    m = score_release(q, release)
    assert m.position == "A2"
    assert m.accepted


def test_the_wrong_artist_is_not_accepted_however_good_the_title() -> None:
    q = Query("Aero", "Come Again")
    m = score_release(q, _release({"position": "1", "title": "Come Again"}, artist="Madonna"))
    assert not m.accepted


def test_a_track_far_off_in_length_loses_confidence() -> None:
    q = Query("Aero", "Come Again", seconds=400)
    near = score_release(q, _release({"position": "1", "title": "Come Again", "duration": "6:41"}))
    far = score_release(q, _release({"position": "1", "title": "Come Again", "duration": "3:00"}))
    assert near.confidence > far.confidence


def test_headings_in_a_tracklist_are_not_tracks() -> None:
    q = Query("Aero", "Side A")
    release = {
        "artists": [{"name": "Aero"}],
        "tracklist": [{"type_": "heading", "title": "Side A"}],
    }
    assert score_release(q, release).confidence == 0.0


def test_the_artists_own_release_ranks_above_a_compilation() -> None:
    q = Query("Aero", "Come Again")
    results = [
        {"id": 1, "title": "Various - Techno Hits 2024"},
        {"id": 2, "title": "Aero - Straight From The Shoulder EP"},
    ]
    assert [r["id"] for _s, r in rank_candidates(q, results)] == [2, 1]


def test_one_of_several_artists_is_enough() -> None:
    q = Query("Oscar Mulero", "Diode")
    ((score, _r),) = rank_candidates(q, [{"id": 1, "title": "Kastil, Oscar Mulero - Diode"}])
    assert score == 1.0


def test_the_original_year_is_the_earliest_on_the_same_master() -> None:
    results = [
        {"id": 1, "master_id": 9, "year": "2016"},
        {"id": 2, "master_id": 9, "year": "1994"},
        {"id": 3, "master_id": 7, "year": "1980"},  # another record
        {"id": 4, "master_id": 9, "year": ""},
    ]
    assert original_year(2016, 9, results) == 1994
    assert original_year(2016, None, results) == 2016
    assert original_year(None, None, []) is None


def test_letters_without_a_decomposition_are_folded_too() -> None:
    assert norm_artist("Øyvind Æsir") == "oyvind aesir"
    assert norm_title("Straße") == "strasse"
    assert norm_artist("ANNĒ") == "anne"  # a macron decomposes on its own


def test_formats_rank_digital_then_cd_then_vinyl() -> None:
    from core.discogs_match import format_rank

    assert format_rank({"format": ["File", "WAV", "EP"]}) == 0
    assert format_rank({"format": ["CD", "Album"]}) == 1
    assert format_rank({"format": ["Vinyl", '12"']}) == 2
    assert format_rank({"format": ["Cassette"]}) == 3
    assert format_rank({}) == 3


def test_among_equal_pressings_the_digital_one_ranks_first() -> None:
    q = Query("Aero", "Come Again")
    results = [
        {"id": 1, "title": "Aero - EP", "format": ["Vinyl", '12"']},
        {"id": 2, "title": "Aero - EP", "format": ["CD"]},
        {"id": 3, "title": "Aero - EP", "format": ["File", "MP3"]},
    ]
    assert [r["id"] for _s, r in rank_candidates(q, results)] == [3, 2, 1]


def test_a_better_match_still_beats_a_digital_one() -> None:
    q = Query("Aero", "Come Again")
    results = [
        {"id": 1, "title": "Aero - EP", "format": ["Vinyl"]},
        {"id": 2, "title": "Someone Else - EP", "format": ["File"]},
    ]
    assert [r["id"] for _s, r in rank_candidates(q, results)][0] == 1


def test_within_a_format_the_preferred_region_wins_then_worldwide() -> None:
    q = Query("Aero", "Come Again")
    results = [
        {"id": 1, "title": "Aero - EP", "format": ["CD"], "country": "US"},
        {"id": 2, "title": "Aero - EP", "format": ["CD"], "country": "Worldwide"},
        {"id": 3, "title": "Aero - EP", "format": ["CD"], "country": "Germany"},
    ]
    assert [r["id"] for _s, r in rank_candidates(q, results)] == [3, 2, 1]  # Europe default
    assert [r["id"] for _s, r in rank_candidates(q, results, "north_america")] == [1, 2, 3]
    assert [r["id"] for _s, r in rank_candidates(q, results, "")] == [1, 2, 3]  # Any: as listed


def test_format_comes_before_region() -> None:
    q = Query("Aero", "Come Again")
    results = [
        {"id": 1, "title": "Aero - EP", "format": ["Vinyl"], "country": "UK"},
        {"id": 2, "title": "Aero - EP", "format": ["File"], "country": "US"},
    ]
    assert [r["id"] for _s, r in rank_candidates(q, results)][0] == 2


def test_the_title_decides_before_the_length() -> None:
    """A 90-track compilation always has some track of the right length."""
    q = Query("Datassette/Datashat", "Die Autos (2014)", seconds=299)
    release = {
        "artists": [{"name": "Datassette"}],
        "tracklist": [
            {"type_": "track", "position": "80", "title": "Packing Peanuts (2016)",
             "duration": "4:59", "artists": [{"name": "Datassette"}]},
            {"type_": "track", "position": "12", "title": "Die Autos",
             "duration": "6:10", "artists": [{"name": "Datashat"}]},
        ],
    }  # fmt: skip
    m = score_release(q, release)
    assert m.position == "12"


def test_no_close_title_is_no_match() -> None:
    q = Query("Datassette", "Die Autos", seconds=299)
    release = {
        "artists": [{"name": "Datassette"}],
        "tracklist": [{"type_": "track", "position": "1", "title": "Packing Peanuts",
                       "duration": "4:59"}],
    }  # fmt: skip
    m = score_release(q, release)
    assert m.position == "" and not m.accepted


def test_each_name_in_our_artist_field_counts() -> None:
    q = Query("Datassette/Datashat", "X")
    ((score, _r),) = rank_candidates(q, [{"id": 1, "title": "Datashat - EP"}])
    assert score == 1.0


def test_a_bracketed_year_is_ignored() -> None:
    assert norm_title("Die Autos (2014)") == norm_title("Die Autos")
    assert norm_title("Song 2014") == "song 2014"  # only in brackets


def test_one_title_inside_the_other_is_a_strong_match() -> None:
    from core.discogs_match import title_similarity

    long_ = "Songs from Liquid Days: No. 1, Changing Opinion"
    assert title_similarity(long_, "Changing Opinion") == 0.9
    assert title_similarity("Days", "Changing Days") < 0.9  # one short word is not enough
    assert title_similarity("Opinion", "Changing Opinion") == 0.9  # one of 6+ letters is


def test_a_title_of_punctuation_is_compared_as_typed() -> None:
    from core.discogs_match import title_similarity

    assert title_similarity("////\\\\\\\\", "////\\\\\\\\") == 1.0
    assert title_similarity("////", "\\\\\\\\") == 0.0
