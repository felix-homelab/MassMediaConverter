"""Edition-tag detection for the renamer.

Recognizes a small, configurable allowlist of editions (by default
Director's Cut, Extended, Remastered and Uncut) and strips them from the
filename before the metadata lookup. Non-allowed edition labels are stripped
too, but only when they appear after the year/episode marker or inside a
tag-only bracket group, so titles like "The Final Cut" survive.

Multi-part markers (CD1, Part 1, ...) are intentionally not handled here.
"""

import re

EDITION_PLACEHOLDER = "[EDITION]"


def normalize_edition_key(value):
    """Normalize a label for comparisons: case/punctuation/spacing-insensitive."""
    if not isinstance(value, str):
        return ""
    value = value.replace("\u2019", "'").lower()
    return re.sub(r"[^a-z0-9]+", "", value)


def _token_pattern(pattern):
    """Wrap a pattern so it only matches whole tokens."""
    return rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])"


# Built-in groups. "keys" decide whether a configured tag belongs to the
# group; "patterns" describe the source spellings that map to it.
_GROUPS = (
    {
        "canonical": "Director's Cut",
        "keys": {"directorscut", "directorcut", "dc"},
        "patterns": (
            r"director'?s[\s._-]*cut(?:[\s._-]*(?:edition|version))?",
            r"director[\s._-]*cut(?:[\s._-]*(?:edition|version))?",
            r"dc",
        ),
    },
    {
        "canonical": "Extended",
        "keys": {"extended", "extendedcut", "extendededition", "extendedversion"},
        "patterns": (
            r"extended(?:[\s._-]*(?:cut|edition|version))?",
        ),
    },
    {
        "canonical": "Remastered",
        "keys": {"remastered", "remaster", "4kremastered", "4kremaster"},
        "patterns": (
            r"(?:4k[\s._-]*)?remaster(?:ed)?(?:[\s._-]*(?:edition|version))?",
        ),
    },
    {
        "canonical": "Uncut",
        "keys": {"uncut", "uncutversion", "uncutedition"},
        "patterns": (
            r"uncut(?:[\s._-]*(?:version|edition))?",
        ),
    },
)

# Labels that are always dropped (never written to [EDITION]). They are only
# stripped from the parsed title when after the year/episode marker or in a
# tag-only bracket group, which protects titles such as "The Final Cut" and
# "Uncut Gems".
_IGNORED_PATTERNS = tuple(sorted((
    r"unrated",
    r"theatrical(?:[\s._-]*cut)?",
    r"imax(?:[\s._-]*enhanced)?",
    r"criterion(?:[\s._-]*collection)?",
    r"special[\s._-]*edition",
    r"ultimate[\s._-]*edition",
    r"final[\s._-]*cut",
    r"open[\s._-]*matte",
    r"restored",
    r"anniversary[\s._-]*edition",
    r"collector'?s[\s._-]*edition",
    r"limited[\s._-]*edition",
), key=len, reverse=True))

_IGNORED_RE = re.compile(
    _token_pattern("|".join(_IGNORED_PATTERNS)), re.IGNORECASE)

# Tokens that may legitimately follow an edition tag. They make the
# "balanced" rule accept e.g. "Movie.Extended.German.DL.1080p" while
# rejecting "The Extended Family".
_RELEASE_MARKER_RE = re.compile(
    r"(?:"
    r"(?:19|20)\d{2}"                                      # year
    r"|\d{3,4}p|4k|8k|uhd"                                 # resolution
    r"|blu-?ray|bd(?:rip)?|br(?:rip)?|web-?dl|webrip|web"
    r"|hdtv|dvd(?:rip)?|remux|hdcam|cam"                   # source
    r"|x26[45]|h\.?26[45]|hevc|avc|av1|vp9|mpeg-?2|divx|xvid"  # video codec
    r"|aac|ac3|eac3|dts(?:-?hd)?|truehd|atmos|flac|opus|mp3|ddp?|dl"  # audio
    r"|hdr(?:10)?|dolby[\s._-]*vision|dv|sdr|10bit|8bit"   # HDR
    r"|german|english|french|italian|spanish|multi(?:lingual)?"
    r"|dual|dubbed|subbed|subs?"                           # language tags
    r"|proper|repack|internal"                             # release tags
    r")",
    re.IGNORECASE,
)

# Prefixes that commonly appear immediately before "-GROUP" in scene names.
_RELEASE_PREFIX_RE = re.compile(
    r"(?:x26[45]|h\.?26[45]|hevc|avc|av1|vp9"
    r"|web(?:-?dl|-?rip)?|blu-?ray|bd(?:rip)?|br(?:rip)?"
    r"|hdtv|dvd(?:rip)?|remux|aac|ac3|eac3|dts|truehd|atmos"
    r"|flac|opus|mp3|ddp?|10bit|8bit|hdr(?:10)?|dv"
    r"|\d{3,4}p|4k|8k|uhd)",
    re.IGNORECASE,
)

_SEPARATORS = " \t\r\n._-([{"


class _Matcher:
    __slots__ = ("label", "regex")

    def __init__(self, label, pattern):
        self.label = label
        self.regex = re.compile(_token_pattern(pattern), re.IGNORECASE)


def _pattern_for_label(label):
    """Build a flexible pattern for a user-supplied custom label."""
    words = [re.escape(part) for part in re.split(r"[\s._-]+", label.strip()) if part]
    if not words:
        return None
    body = r"[\s._-]*".join(words)
    return body + r"(?:[\s._-]*(?:edition|version))?"


def _build_matchers(edition_tags):
    matchers = []
    used_groups = set()

    for tag in edition_tags or []:
        if not isinstance(tag, str) or not tag.strip():
            continue
        label = " ".join(tag.split())
        key = normalize_edition_key(label)

        group_index = None
        for index, group in enumerate(_GROUPS):
            if key in group["keys"]:
                group_index = index
                break

        if group_index is not None:
            if group_index in used_groups:
                continue
            used_groups.add(group_index)
            for pattern in _GROUPS[group_index]["patterns"]:
                matchers.append(_Matcher(label, pattern))
        else:
            pattern = _pattern_for_label(label)
            if pattern:
                matchers.append(_Matcher(label, pattern))

    return matchers


def _overlaps(spans, start, end):
    return any(start < span_end and end > span_start
               for span_start, span_end in spans)


def _inside_release_group(stem, start):
    """True when the match lies in the final "-RELEASEGROUP" suffix."""
    dash = stem.rfind("-")
    if dash == -1 or start < dash + 1:
        return False

    group = stem[dash + 1:]
    if not group or re.search(r"[\s._]", group):
        return False

    before = stem[:dash]
    tokens = re.split(r"[\s._]+", before)
    token = tokens[-1] if tokens else ""
    return bool(_RELEASE_PREFIX_RE.fullmatch(token))


def _starts_with_edition(text, matchers):
    if _IGNORED_RE.match(text):
        return True
    return any(matcher.regex.match(text) for matcher in matchers)


def _following_ok(stem, end, matchers):
    rest = stem[end:]
    raw = rest.strip()

    # A final "-GROUP" release suffix is allowed after an edition tag.
    if re.fullmatch(r"-[A-Za-z0-9]+", raw):
        return True

    stripped = rest.lstrip(_SEPARATORS)
    if not stripped:
        return True
    if stripped[0] in "])}":
        return True
    if _RELEASE_MARKER_RE.match(stripped):
        return True
    if _starts_with_edition(stripped, matchers):
        return True
    return False


def _anchor_end(stem):
    """End of the year or SxxExx marker, whichever is later."""
    ends = []
    year = re.search(r"\b(19|20)\d{2}\b", stem)
    if year:
        ends.append(year.end())
    episode = re.search(
        r"(?<![A-Za-z0-9])[sS]\d{1,2}[eE]\d{1,2}(?![A-Za-z0-9])", stem)
    if episode:
        ends.append(episode.end())
    return max(ends) if ends else None


def _bracket_remainder_is_junk(remainder, matchers):
    """True when a bracket contains nothing but release/edition noise."""
    text = remainder.strip()
    if not text:
        return True

    for matcher in matchers:
        text = matcher.regex.sub(" ", text)
    text = _IGNORED_RE.sub(" ", text)

    tokens = [token for token in re.split(r"[\s._-]+", text) if token]
    return all(_RELEASE_MARKER_RE.fullmatch(token) for token in tokens)


def _in_tag_only_brackets(stem, start, end, matchers):
    for open_char, close_char in (("[", "]"), ("(", ")")):
        open_index = stem.rfind(open_char, 0, start)
        close_index = stem.find(close_char, end)
        if open_index == -1 or close_index == -1 or close_index < end:
            continue

        inner = stem[open_index + 1:close_index]
        rel_start = start - (open_index + 1)
        rel_end = end - (open_index + 1)
        if rel_start < 0 or rel_end > len(inner):
            continue

        remainder = inner[:rel_start] + inner[rel_end:]
        if _bracket_remainder_is_junk(remainder, matchers):
            return True

    return False


def _remove_spans(stem, spans):
    if not spans:
        return stem
    chars = list(stem)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    return "".join(chars)


def detect_editions(stem, edition_tags):
    """Return (cleaned_stem, ordered_labels).

    An empty allowlist disables all edition processing.
    """
    if not edition_tags:
        return stem, []
    if not isinstance(edition_tags, (list, tuple)):
        edition_tags = [edition_tags]

    matchers = _build_matchers(edition_tags)
    if not matchers:
        return stem, []

    # Treat typographic apostrophes like plain ones for alias matching.
    stem = stem.replace("\u2019", "'")

    allowed_spans = []
    labels = []

    for matcher in matchers:
        for match in matcher.regex.finditer(stem):
            start, end = match.span()
            if _inside_release_group(stem, start):
                continue
            if not _following_ok(stem, end, matchers):
                continue
            if _overlaps(allowed_spans, start, end):
                continue

            allowed_spans.append((start, end))
            if matcher.label not in labels:
                labels.append(matcher.label)

    anchor = _anchor_end(stem)
    ignored_spans = []

    for match in _IGNORED_RE.finditer(stem):
        start, end = match.span()
        if _overlaps(allowed_spans, start, end):
            continue
        if _inside_release_group(stem, start):
            continue

        post_anchor = anchor is not None and start >= anchor
        bracketed = _in_tag_only_brackets(stem, start, end, matchers)
        if post_anchor or bracketed:
            ignored_spans.append((start, end))

    if not allowed_spans and not ignored_spans:
        return stem, []

    return _remove_spans(stem, allowed_spans + ignored_spans), labels
