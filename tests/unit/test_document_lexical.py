from hardy.documents import completion, syntax


def test_stamp_ignores_inactive_document_markers():
    source = '% \\begin{document}\n\\newcommand{\\x}{\\begin{document}}\n\\iffalse\\begin{document}\\fi\n\\begin{document}Body'
    result = syntax.stamped(source, 'STAMP')
    assert result.startswith(source[:source.rindex('\\begin{document}')])
    assert result.index('STAMP') > result.rindex('\\begin{document}')


def test_completion_ignores_inactive_inclusions():
    tex = {'writeup.tex': '\\verb|\\input{ghost}|\n\\newcommand{\\x}{\\input{ghost}}\n', 'ghost.tex': '\\begin{verbatim}theorem t : True\\end{verbatim}'}
    assert 'theorem t : True' not in completion.quoted_lean(completion.assemble(tex))


def test_inline_and_escaped_openers_do_not_hide_theorems():
    for opener in ('\\verb|\\begin{verbatim}|', '\\\\begin{verbatim}'):
        document = completion.displayed(opener + '\n\\newtheorem{theorem}{Theorem}\n\\begin{theorem}Claim\\end{theorem}')
        assert completion.asserted_theorems(document) == (('theorem', ''),)


def test_unreached_walks_each_file_once(monkeypatch):
    calls = []
    original = syntax._executed
    def counted(source, conditionals=syntax.NO_CONDITIONALS):
        calls.append(source)
        return original(source, conditionals)
    monkeypatch.setattr(syntax, '_executed', counted)
    tex = {'writeup.tex': '\\input{a}\\input{b}', 'a.tex': 'A', 'b.tex': 'B'}
    assert syntax.unreached_fragments(tex) == []
    assert len(calls) == len(tex)


def test_percent_delimited_inline_quote_survives_comment_scan():
    assert completion.quoted_lean(completion.displayed(r'\verb%theorem t : True%')) == ('theorem t : True',)


def test_spaced_verbatim_opener_and_nested_false_branch():
    source = r'\iffalse\ifnum0=1 x\fi\begin{verbatim}hidden\end{verbatim}\fi' + '\n' + r'\begin {verbatim}theorem t : True\end{verbatim}'
    assert completion.quoted_lean(completion.displayed(source)) == ('theorem t : True',)


def test_active_marker_offsets_preserve_escaped_percent_and_backslash():
    for prefix in ('\\% ', '\\\\% fake \\begin{document}\n'):
        source = prefix + r'\begin{document}Body'
        match = syntax.executed_begin_document(source)
        assert match is not None
        assert match.start() == source.rindex(r'\begin{document}')


def test_displayed_runs_map_live_text_after_comments_and_listings():
    source = '% comment\r\n\\begin{verbatim}hidden\\end{verbatim}\\input{a}\n'
    page = completion.displayed(source)
    at = page.executed.index(r'\input{a}')
    assert any(start <= at < start + length and raw + at - start == source.index(r'\input{a}') for start, raw, length in page.runs)


def test_starred_verbatim_environment_still_quotes_lean():
    assert completion.quoted_lean(completion.displayed(r'\begin{Verbatim*}theorem t : True\end{Verbatim*}')) == ('theorem t : True',)


def test_lstinline_still_quotes_without_following_inclusion():
    tex = {'writeup.tex': r'\lstinline|\input{ghost}|', 'ghost.tex': r'\begin{verbatim}theorem t : True\end{verbatim}'}
    assert completion.quoted_lean(completion.assemble(tex)) == (r'\input{ghost}',)


def test_completion_and_reachability_agree_on_executed_commands():
    sources = (
        r'\verb|\begin{verbatim}| \input{a}',
        r'\\begin{verbatim} \input{a}',
        r'\newcommand{\x}{\input{ghost}} \input{a}',
        r'\def\x{\begin{verbatim}} \input{a}',
        r'\iffalse\ifnum0=1 x\fi\input{ghost}\fi\input{a}',
        r'\begin {verbatim}\input{ghost}\end{verbatim}\input{a}',
        '\\verb%literal% % comment\n\\input{a}',
    )
    for source in sources:
        assert completion.normalise(completion.displayed(source).executed) == completion.normalise(syntax._executed(source))


def test_false_branch_macro_never_changes_live_scanner_state():
    prefix = r'\iffalse\def\x{\fi'
    source = prefix + r'\begin{document}Body'
    marker = syntax.executed_begin_document(source)
    assert marker is not None and marker.start() == len(prefix)
    tex = {'writeup.tex': prefix + r'\input{a}', 'a.tex': r'\begin{verbatim}theorem t : True\end{verbatim}'}
    assert syntax.reached_fragments(tex) == set(tex)
    assert completion.quoted_lean(completion.assemble(tex)) == ('theorem t : True',)


def test_document_marker_is_never_a_let_operand_or_optional_default():
    assert syntax.executed_begin_document(r'\let\x\begin{document}Body') is None
    source = r'\newcommand{\x}[1][\begin{document}]{#1}\begin{document}Body'
    marker = syntax.executed_begin_document(source)
    assert marker is not None and marker.start() == source.rindex(r'\begin{document}')


def test_listing_immediately_before_include_remains_quoted():
    tex = {'writeup.tex': r'\begin{verbatim}theorem t : True\end{verbatim}\input{a}', 'a.tex': 'A'}
    assert completion.quoted_lean(completion.assemble(tex)) == ('theorem t : True',)


def test_literal_form_feed_is_not_a_line_ending():
    source = 'Intro\fduction\u2028more\n'
    assert completion.displayed(source).executed == source


def test_transforming_inline_listing_cannot_credit_a_quotation():
    source = r'\lstinline[literate={True}{{False}}4]|theorem t : True|'
    assert completion.quoted_lean(completion.displayed(source)) == ()


def test_verb_bracket_delimiter_has_no_optional_arguments():
    tex = {
        'writeup.tex': r'\begin{document}\verb[demo]|x| \input{ghost}[\label{t}\end{document}',
        'ghost.tex': r'\begin{verbatim}theorem t : True\end{verbatim}',
    }
    assert syntax.reached_fragments(tex) == {'writeup.tex'}
    assert completion.quoted_lean(completion.assemble(tex)) == (r'demo]|x| \input{ghost}',)
    obligations = completion.outstanding(
        theorems={'t': 'theorem t : True'},
        registry=[{'formal_name': 't', 'latex_name': 't'}],
        labels={'t'}, assumptions=[], used=set(), tex=tex,
    )
    assert tuple(item.kind for item in obligations) == ('statement',)


def test_starred_verb_accepts_alphabetic_and_star_delimiters():
    for delimiter in ('A', '*'):
        tex = {
            'writeup.tex': rf'\begin{{document}}\verb*{delimiter}\input{{ghost}}{delimiter}\label{{t}}\end{{document}}',
            'ghost.tex': r'\begin{verbatim}theorem t : True\end{verbatim}',
        }
        assert syntax.reached_fragments(tex) == {'writeup.tex'}
        assert completion.quoted_lean(completion.assemble(tex)) == (r'\input{ghost}',)


def test_braced_lstinline_preserves_following_live_theorem():
    source = r'\lstinline{code}\newtheorem{theorem}{Theorem}\begin{theorem}Claim\end{theorem}'
    displayed = completion.displayed(source)
    assert completion.quoted_lean(displayed) == ('code',)
    assert completion.asserted_theorems(displayed) == (('theorem', ''),)
    obligations = completion.outstanding(
        theorems={}, registry=[], labels=set(), assumptions=[], used=set(),
        tex={'writeup.tex': source},
    )
    assert tuple(item.kind for item in obligations) == ('theorem',)


def test_braced_lstinline_quotes_literal_command_and_reaches_live_include():
    tex = {
        'writeup.tex': r'\lstinline{\input ghost}\input{live}',
        'ghost.tex': r'\begin{verbatim}theorem ghost : True\end{verbatim}',
        'live.tex': r'\begin{verbatim}theorem live : True\end{verbatim}',
    }
    assert syntax.reached_fragments(tex) == {'writeup.tex', 'live.tex'}
    assert completion.quoted_lean(completion.assemble(tex)) == (r'\input ghost', 'theorem live : True')


def test_braced_lstinline_closes_after_literal_backslash():
    source = r'\lstinline{\}\newtheorem{theorem}{Theorem}\begin{theorem}Claim\end{theorem}'
    assert completion.asserted_theorems(completion.displayed(source)) == (('theorem', ''),)


def test_braced_lstinline_treats_opening_brace_as_literal_content():
    source = r'\lstinline{{code}\newtheorem{theorem}{Theorem}\begin{theorem}Claim\end{theorem}'
    displayed = completion.displayed(source)
    assert completion.quoted_lean(displayed) == ('{code',)
    assert completion.asserted_theorems(displayed) == (('theorem', ''),)


def test_escaped_fi_never_ends_a_false_branch():
    tex = {
        'writeup.tex': r'\iffalse\\fi\input{ghost}\fi\input{live}',
        'ghost.tex': r'\begin{verbatim}theorem ghost : True\end{verbatim}',
        'live.tex': r'\begin{verbatim}theorem live : True\end{verbatim}',
    }
    assert syntax.reached_fragments(tex) == {'writeup.tex', 'live.tex'}
    assert completion.quoted_lean(completion.assemble(tex)) == ('theorem live : True',)


def test_dormant_inline_command_never_swallows_macro_closing_brace():
    for command in ('verb', 'lstinline'):
        prefix = rf'\def\x{{\{command}|}}|'
        source = prefix + r'\newtheorem{theorem}{Theorem}\begin{theorem}Claim\end{theorem}\input{live}'
        tex = {'writeup.tex': source, 'live.tex': 'Live.'}
        assert syntax.reached_fragments(tex) == set(tex)
        assert completion.asserted_theorems(completion.displayed(source)) == (('theorem', ''),)


def test_dormant_let_never_swallows_macro_closing_brace():
    source = r'\newtheorem{theorem}{Theorem}\def\x{\let\y}\begin{theorem}Claim\end{theorem}\input{live}'
    tex = {'writeup.tex': source, 'live.tex': r'\begin{verbatim}theorem t : True\end{verbatim}'}
    assert completion.asserted_theorems(completion.displayed(source)) == (('theorem', ''),)
    assert completion.quoted_lean(completion.assemble(tex)) == ('theorem t : True',)


def test_cr_and_crlf_are_tex_line_endings_with_original_marker_offsets():
    for ending in ('\r', '\r\n', '\n'):
        root = '% comment' + ending + r'\input{live}' + ending
        tex = {'writeup.tex': root, 'live.tex': 'Live.'}
        assert syntax.reached_fragments(tex) == set(tex)
        assert 'Live.' in completion.assemble(tex).executed
        source = '% fake \\begin{document}' + ending + r'\begin{document}Body'
        marker = syntax.executed_begin_document(source)
        assert marker is not None and marker.start() == source.rindex(r'\begin{document}')
