"""Restore AMC's streamed HTML before walking movie sections."""

import re


_SEGMENT_CALL = re.compile(r'\$RS\("(S:[^"\s]+)"\s*,\s*"(P:[^"\s]+)"\)')


def restore_streamed_segments(soup):
    """Apply React's $RS segment insertions without executing page JavaScript.

    AMC sends some showtime cells in hidden S:* containers outside the movie
    section, then uses $RS to replace P:* template placeholders with them.
    Restore their original positions so movie, format, and ordering survive.
    Missing fragments are left alone (for example, an incomplete response).
    """
    by_id = {node["id"]: node for node in soup.find_all(id=True)}
    for script in soup.find_all("script"):
        for source_id, target_id in _SEGMENT_CALL.findall(script.string or ""):
            source, target = by_id.get(source_id), by_id.get(target_id)
            if source is None or target is None:
                continue
            if (source.name != "div" or not source.has_attr("hidden")
                    or target.name != "template" or target.parent is None):
                continue
            for child in list(source.contents):
                target.insert_before(child.extract())
            target.extract()
            source.extract()
