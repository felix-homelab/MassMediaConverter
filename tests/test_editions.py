import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.util_editions import detect_editions

DEFAULT_TAGS = ["Director's Cut", "Extended", "Remastered", "Uncut"]


class EditionDetectionTests(unittest.TestCase):
    def labels(self, stem, tags=None):
        return detect_editions(stem, tags or DEFAULT_TAGS)[1]

    def cleaned(self, stem, tags=None):
        return detect_editions(stem, tags or DEFAULT_TAGS)[0]

    def test_directors_cut_variants(self):
        for stem in (
            "Movie.2020.Directors.Cut.1080p",
            "Movie.2020.Director's Cut.1080p",
            "Movie.2020.Director.Cut.1080p",
            "Movie.2020.DC.1080p",
        ):
            self.assertEqual(self.labels(stem), ["Director's Cut"], stem)

    def test_typographic_apostrophe(self):
        self.assertEqual(
            self.labels("Movie.2020.Director\u2019s.Cut.1080p"),
            ["Director's Cut"])

    def test_extended_variants(self):
        for stem in (
            "Movie.2020.Extended.1080p",
            "Movie.2020.Extended.Cut.1080p",
            "Movie.2020.Extended.Edition.1080p",
            "Movie.Extended.2020.1080p",
        ):
            self.assertEqual(self.labels(stem), ["Extended"], stem)

    def test_remastered_variants(self):
        for stem in (
            "Movie.2020.Remastered.1080p",
            "Movie.2020.Remaster.1080p",
            "Movie.2020.4K.Remastered.2160p",
        ):
            self.assertEqual(self.labels(stem), ["Remastered"], stem)

    def test_uncut_variants(self):
        for stem in (
            "Movie.2020.Uncut.1080p",
            "Movie.2020.Uncut.Version.1080p",
            "Movie.2020.Uncut.Edition.1080p",
        ):
            self.assertEqual(self.labels(stem), ["Uncut"], stem)

    def test_multiple_editions_follow_config_order(self):
        labels = self.labels("Movie.2019.Uncut.Extended.Remastered.2160p")
        self.assertEqual(labels, ["Extended", "Remastered", "Uncut"])

    def test_balanced_rule_protects_real_titles(self):
        for stem in ("The Extended Family S01E01", "Uncut Gems 2019 1080p"):
            self.assertEqual(self.labels(stem), [], stem)
            self.assertEqual(self.cleaned(stem), stem, stem)

    def test_release_group_is_not_an_edition(self):
        self.assertEqual(self.labels("Movie.2020.1080p.x264-DC"), [])

    def test_edition_before_release_group(self):
        self.assertEqual(self.labels("Movie.2020.Extended-RARBG"), ["Extended"])

    def test_edition_followed_by_language_tags(self):
        self.assertEqual(self.labels("Movie.2020.Extended.German.DL.1080p"), ["Extended"])

    def test_edition_is_stripped_from_cleaned_stem(self):
        cleaned = self.cleaned("Movie.2020.Extended.1080p")
        self.assertNotIn("Extended", cleaned)
        self.assertIn("Movie", cleaned)

    def test_ignored_label_after_year_is_stripped(self):
        cleaned = self.cleaned("Movie.2020.Unrated.1080p")
        self.assertNotIn("Unrated", cleaned)

    def test_ignored_label_before_year_is_protected(self):
        stem = "The.Final.Cut.2004.1080p"
        self.assertEqual(self.cleaned(stem), stem)
        self.assertEqual(self.labels(stem), [])

    def test_tag_only_bracket_is_stripped(self):
        cleaned = self.cleaned("Movie [Final Cut] 2004")
        self.assertNotIn("Final Cut", cleaned)

    def test_bracket_with_title_words_is_protected(self):
        stem = "Movie [The Final Cut] 2004"
        self.assertEqual(self.cleaned(stem), stem)

    def test_empty_allowlist_disables_processing(self):
        stem = "Movie.2020.Extended.1080p"
        self.assertEqual(detect_editions(stem, []), (stem, []))

    def test_custom_label(self):
        self.assertEqual(self.labels("Movie.2020.Final.Cut.1080p", ["Final Cut"]), ["Final Cut"])

    def test_configured_spelling_is_used(self):
        self.assertEqual(self.labels("Movie.2020.Directors.Cut.1080p", ["DC"]), ["DC"])


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="mmc_test_")
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_formats_get_edition_placeholder(self):
        from utils.util_config import load_config, save_config

        save_config({
            "converter": {
                "convert_to": "mp4", "threads": 2, "resolution": "original",
                "scaling_mode": "both",
            },
            "renamer": {
                "video_extensions": [".mkv"],
                "movie_format": "[MOVIE_NAME] ([YEAR]) - [RESOLUTION]",
                "show_format": "[SHOW_NAME] - S[SEASON]E[EPISODE] - [EPISODE_NAME]",
                "collision_suffix": " ({n})",
            },
        })

        config = load_config()
        self.assertEqual(config["config_version"], 2)
        self.assertEqual(
            config["renamer"]["movie_format"],
            "[MOVIE_NAME] ([YEAR]) - [EDITION] - [RESOLUTION]")
        self.assertEqual(
            config["renamer"]["show_format"],
            "[SHOW_NAME] - S[SEASON]E[EPISODE] - [EPISODE_NAME] - [EDITION]")
        self.assertEqual(
            config["renamer"]["edition_tags"],
            ["Director's Cut", "Extended", "Remastered", "Uncut"])

    def test_reload_is_idempotent(self):
        from utils.util_config import load_config

        first = load_config()["renamer"]["movie_format"]
        second = load_config()["renamer"]["movie_format"]
        self.assertEqual(first, second)
        self.assertEqual(second.count("[EDITION]"), 1)

    def test_removed_placeholder_is_not_readded(self):
        from utils.util_config import load_config, save_config

        config = load_config()
        config["renamer"]["movie_format"] = "[MOVIE_NAME] ([YEAR]) - [RESOLUTION]"
        save_config(config)

        self.assertEqual(
            load_config()["renamer"]["movie_format"],
            "[MOVIE_NAME] ([YEAR]) - [RESOLUTION]")

    def test_edition_tags_are_deduplicated(self):
        from utils.util_config import load_config, save_config, _validate_config

        config = load_config()
        config["renamer"]["edition_tags"] = [
            "Director's Cut", "directors cut", "Uncut", "  ", "Extended"]
        validated = _validate_config(config)
        self.assertEqual(
            validated["renamer"]["edition_tags"],
            ["Director's Cut", "Uncut", "Extended"])

    def test_future_config_version_is_preserved(self):
        from utils.util_config import load_config, _validate_config

        config = load_config()
        config["config_version"] = 99
        self.assertEqual(_validate_config(config)["config_version"], 99)

    def test_empty_list_disables_editions(self):
        from utils.util_config import load_config, _validate_config

        config = load_config()
        config["renamer"]["edition_tags"] = []
        self.assertEqual(_validate_config(config)["renamer"]["edition_tags"], [])


if __name__ == "__main__":
    unittest.main()
