"""Tests for the real-repository benchmark's query construction.

The value of that benchmark rests entirely on two properties: the query text
must not appear in the searched corpus, and a query must not name the file that
answers it. Both are asserted here, because a silent regression in either would
inflate the reported numbers rather than fail loudly.
"""

import io
import zipfile

import pytest

from benchmarks.real_world import (
    _is_usable_query,
    _summary_sentence,
    _symbol_tokens,
    extract_queries,
    read_repository,
    strip_docstrings,
)


def test_docstrings_are_removed_from_indexed_source():
    source = '''
def charge_card(token, amount):
    """Capture an authorised payment against the stored card."""
    return gateway.capture(token, amount)
'''

    stripped = strip_docstrings(source)

    assert "Capture an authorised payment" not in stripped
    assert "return gateway.capture(token, amount)" in stripped


def test_stripping_preserves_line_count_so_line_numbers_stay_valid():
    source = '"""Module.\n\nLong.\n"""\n\n\ndef f():\n    """Doc."""\n    return 1\n'

    assert len(strip_docstrings(source).splitlines()) == len(source.splitlines())


def test_stripping_leaves_unparseable_source_untouched():
    source = "def broken(:\n    pass\n"

    assert strip_docstrings(source) == source


def test_class_docstrings_are_removed_too():
    source = 'class Client:\n    """Talks to the upstream service."""\n\n    timeout = 5\n'

    stripped = strip_docstrings(source)

    assert "Talks to the upstream" not in stripped
    assert "timeout = 5" in stripped


def test_summary_sentence_takes_only_the_first_sentence_of_the_first_paragraph():
    docstring = "Send the request.\nRetries on failure.\n\nArgs:\n    url: target"

    assert _summary_sentence(docstring) == "Send the request."


def test_symbol_tokens_split_both_naming_conventions():
    assert _symbol_tokens("get_auth_from_url") == {"auth", "from"}
    assert _symbol_tokens("HTTPAdapter") == {"http", "adapter"}


def test_query_naming_its_own_module_is_rejected():
    """A query containing the answer's filename tests nothing."""
    assert not _is_usable_query(
        "Return the parsed session object used by the client.", "requests/sessions.py"
    )
    assert _is_usable_query(
        "Return the parsed object used by the outbound client.", "requests/sessions.py"
    )


@pytest.mark.parametrize(
    "question",
    [
        "Too short.",
        "One two three four five",
        "Run this: >>> client.get(url) and inspect the returned value carefully",
    ],
)
def test_unusable_query_shapes_are_rejected(question):
    assert not _is_usable_query(question, "pkg/module.py")


def test_extract_queries_labels_identifier_free_questions_as_hard():
    files = {
        "pkg/tokens.py": (
            'def issue_token(user_id):\n'
            '    """Create a signed credential that proves who the caller is."""\n'
            '    return sign(user_id)\n'
        ),
        "pkg/billing.py": (
            'def charge_card(token, amount):\n'
            '    """Capture an authorised payment against the stored card."""\n'
            '    return gateway.capture(token, amount)\n'
        ),
    }

    queries = {query.symbol: query for query in extract_queries("o/r", files)}

    # "credential ... caller" shares no token with issue_token.
    assert queries["issue_token"].hard is True
    # "payment against the stored card" contains "card", a token of charge_card.
    assert queries["charge_card"].hard is False


def test_extract_queries_skips_private_and_undocumented_definitions():
    files = {
        "pkg/module.py": (
            'def _private(value):\n'
            '    """Return a normalised copy of the supplied configuration value."""\n'
            '    return value\n'
            'def undocumented(value):\n'
            '    return value\n'
        )
    }

    assert extract_queries("o/r", files) == []


def test_expected_files_cover_every_file_defining_the_symbol():
    body = (
        'def invoke(command):\n'
        '    """Run a registered callback exactly the way the runtime expects."""\n'
        '    return command()\n'
    )
    files = {"pkg/core.py": body, "pkg/testing.py": body}

    queries = extract_queries("o/r", files)

    assert queries
    assert queries[0].expected_files == ("pkg/core.py", "pkg/testing.py")


def test_read_repository_strips_the_archive_prefix_and_skips_binaries():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("click-8.1.7/src/click/core.py", "x = 1\n")
        archive.writestr("click-8.1.7/logo.png", b"\x89PNG\r\n")
        archive.writestr("click-8.1.7/README.md", "# Click\n")

    files = read_repository(buffer.getvalue())

    assert set(files) == {"src/click/core.py", "README.md"}
