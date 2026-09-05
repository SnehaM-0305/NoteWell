"""
Learning modes — Beginner / Medium / Expert.

A single global preference that shapes every AI response in the app (notes,
chat, and practice questions). Set in the UI, stored in localStorage, sent
with every request, and recorded on each note so a note can be read back in
the mode it was written for.

The modes differ on four axes, not just difficulty:
  1. assumed prior knowledge
  2. whether the concrete example comes before or after the general rule
  3. how jargon is handled
  4. what gets omitted

Each mode also carries an explicit accuracy guard, because the failure modes
are asymmetric: Beginner tends to let an analogy replace a fact, and Expert
tends to pad with things the reader already knows.

Three separate things vary by mode, and they are NOT interchangeable:
  * apply_learning_mode() — prose style, appended to a system prompt
  * notes_structure()     — the Markdown skeleton for generated notes
  * question_style()      — how practice questions are calibrated

The structure one matters most. A fixed note template across all three modes
makes the outputs nearly identical no matter how the style instructions are
worded, because the user prompt dictating the shape of the document beats
the system prompt describing its tone.
"""

import textwrap
from typing import Optional

DEFAULT_LEARNING_MODE = "medium"


# ---------------------------------------------------------------------------
# Prose style — appended to system prompts for notes and chat
# ---------------------------------------------------------------------------

_MODES = {
    "beginner": """
LEARNING MODE: BEGINNER
The reader is new to this subject. Assume no background beyond general
literacy.

- Lead with a concrete example or a familiar situation, then name the
  general rule it illustrates. Example first, abstraction second.
- Define every technical term the first time it appears, in one short
  sentence, right where it appears.
- Prefer short sentences and short paragraphs. One idea per sentence.
- Omit edge cases, historical context, and competing schools of thought.
  They are noise at this stage.
- Spell out the steps between two ideas. Do not leave a leap for the
  reader to make.

ACCURACY GUARD: simplifying means saying less, never saying something
untrue. If an analogy would distort the concept, drop the analogy and give
the plain description instead. If you are not confident what a term refers
to — especially an acronym, tool, or library name that could mean several
things — say so plainly and ask, rather than inventing a plausible story.
""",
    "medium": """
LEARNING MODE: MEDIUM
The reader knows the basics of this field and wants a working
understanding they can apply.

- State the rule and give an example of it together.
- Use standard terminology, with a brief gloss on first use for anything
  beyond the fundamentals.
- Include the caveats and edge cases that change what someone would
  actually do. Skip the ones that are merely interesting.
- Explain why something works, not just that it does.
- Where two approaches compete, say which is usually chosen and why.

ACCURACY GUARD: mark uncertainty explicitly. If something is contested,
version-dependent, or you are unsure, say so rather than picking a side by
default.
""",
    "expert": """
LEARNING MODE: EXPERT
The reader is fluent in this field. Their time is the scarce resource.

- Lead with the precise statement. No warm-up, no restating the question.
- Use domain terminology freely and without glossing.
- Omit anything a practitioner already knows. Density is the point.
- Be MORE specific than a general-audience treatment, not less: exact
  names, versions, numbers, mechanisms. Vagueness is the failure mode here,
  not complexity.
- Include the edge cases, failure modes, and trade-offs that a
  non-specialist treatment would skip — that is the value here.
- Where the literature or practice disagrees, name the disagreement
  instead of smoothing it over.

ACCURACY GUARD: an expert reader will notice hand-waving. State
limitations, version dependencies, and the boundaries of what you actually
know. Confident vagueness is worse than an admitted gap.
""",
}

# ---------------------------------------------------------------------------
# Path B: opening / per-section / closing skeletons for TIMED (video/audio)
# sources. Unlike _NOTE_STRUCTURES (one monolithic call for untimed sources),
# these split note-writing into three separate model calls so each chunk's
# section can be written and streamed to the user the moment it's ready,
# instead of waiting for one call that sees the whole transcript.
# ---------------------------------------------------------------------------

_OPENING_STRUCTURES = {
    "beginner": """
        Given the finished sections of these study notes (shown below), write
        the OPENING of the document in Markdown:

        (1) "## The short version" — 2-3 sentences, no jargon, saying what
            this is and why anyone would care.
        (2) "## Start here" — the single core idea, explained from scratch,
            with one concrete example BEFORE any general statement.

        Do not repeat or rewrite the sections themselves -- they are already
        final. Return ONLY the two headings above and their content.
    """,
    "medium": """
        Given the finished sections of these study notes (shown below), write
        the OPENING of the document in Markdown:

        (1) "## TL;DR" — 2-3 sentences using standard terminology,
            summarizing what the sections below actually cover.

        Do not repeat or rewrite the sections themselves -- they are already
        final. Return ONLY the heading above and its content.
    """,
    "expert": """
        Given the finished sections of these reference notes (shown below),
        write the OPENING of the document in Markdown:

        (1) "## Summary" — 2-3 dense sentences. Precise, no warm-up,
            summarizing what the sections below actually cover.

        Do not repeat or rewrite the sections themselves -- they are already
        final. Return ONLY the heading above and its content.
    """,
}

_SECTION_STRUCTURES = {
    "beginner": """
        Write ONE H2 section of study notes in Markdown, covering only the
        source text given to you below -- not the whole document.

        - Begin with a heading in the exact form "## [START–END] Title",
          using the exact bracketed time range given to you (do not compute
          or alter it).
        - Under the heading: short bullets, one idea per bullet.
        - Define every technical term inline the first time it appears.
        - Lead with a concrete example or a familiar situation before naming
          the general rule it illustrates, wherever the source supports that.

        ACCURACY GUARD: simplifying means saying less, never saying something
        untrue. If you are not confident what a term refers to, say so
        plainly rather than inventing a plausible story.
    """,
    "medium": """
        Write ONE H2 section of study notes in Markdown, covering only the
        source text given to you below -- not the whole document.

        - Begin with a heading in the exact form "## [START–END] Title",
          using the exact bracketed time range given to you (do not compute
          or alter it).
        - Under the heading: concise bullets covering both what something
          does and why it works that way.
        - Name specific tools, versions, and alternatives wherever the source
          does.
        - Use standard terminology, with a brief gloss on first use for
          anything beyond the fundamentals.

        ACCURACY GUARD: mark uncertainty explicitly. If something is
        contested, version-dependent, or you are unsure, say so rather than
        picking a side.
    """,
    "expert": """
        Write ONE H2 section of reference notes in Markdown, covering only
        the source text given to you below -- not the whole document.

        - Begin with a heading in the exact form "## [START–END] Title",
          using the exact bracketed time range given to you (do not compute
          or alter it).
        - Under the heading: bullets carrying specific detail -- exact names,
          versions, numbers, mechanisms. One precise sentence beats three
          vague ones.
        - Omit anything a practitioner already knows. Density is the point.
        - Use domain terminology freely and without glossing.

        ACCURACY GUARD: state limitations, version dependencies, and the
        boundaries of what you actually know. Confident vagueness is worse
        than an admitted gap.
    """,
}

_CLOSING_STRUCTURES = {
    "beginner": """
        Given the finished sections of these study notes (shown below), write
        the CLOSING of the document in Markdown:

        (1) "## Words you'll keep seeing" — 6-10 terms from the sections
            above, with one-line plain definitions.
        (2) "## Check yourself" — 5 questions testing whether the reader
            grasped the core ideas covered in the sections above. Recall-level
            is fine here.

        Do not repeat or rewrite the sections themselves -- they are already
        final. Return ONLY the two headings above and their content.
    """,
    "medium": """
        Given the finished sections of these study notes (shown below), write
        the CLOSING of the document in Markdown:

        (1) "## When this matters" — the practical situations where the ideas
            in the sections above change what someone would actually do.
        (2) "## Key terms" — 5-8 terms drawn from the sections above, one line
            each. Skip anything a reader at this level already knows.
        (3) "## Check yourself" — 4 questions requiring application rather
            than recall, based on the sections above.

        Do not repeat or rewrite the sections themselves -- they are already
        final. Return ONLY the three headings above and their content.
    """,
    "expert": """
        Given the finished sections of these reference notes (shown below),
        write the CLOSING of the document in Markdown:

        (1) "## Trade-offs and edge cases" — where the ideas in the sections
            above break down, what they cost, where practice or the
            literature disagrees. This section is the point of the document;
            give it real weight.
        (2) "## Open questions" — what the sections above left unresolved,
            ambiguous, or unaddressed. Omit this section only if there is
            genuinely nothing.
        (3) "## Check yourself" — 3 questions probing boundaries and
            trade-offs based on the sections above, not definitions.

        Do NOT include a glossary -- defining standard terminology for this
        reader is padding.

        Do not repeat or rewrite the sections themselves -- they are already
        final. Return ONLY the sections above and their content.
    """,
}

# ---------------------------------------------------------------------------
# Note structure — the Markdown skeleton, which has to differ by mode
#
# A glossary is essential at Beginner and pure padding at Expert; an
# "open questions" section is useful at Expert and confusing at Beginner.
# Baking one template into build_notes() forces all three modes into the
# same document.
# ---------------------------------------------------------------------------

_NOTE_STRUCTURES = {
    "beginner": """
        Produce study notes in Markdown with this structure:

        (1) "## The short version" — 2-3 sentences, no jargon, saying what
            this is and why anyone would care.
        (2) "## Start here" — the single core idea, explained from scratch,
            with one concrete example BEFORE any general statement.
        (3) H2 sections for each remaining topic, in the order the source introduced them. Under each: short bullets, one idea per bullet.
            Define every technical term inline the first time it appears.
            If the chunk summaries below begin with a bracketed time range like
            [MM:SS–MM:SS], start this section's heading with that exact same
            bracketed range, e.g. "## [12:05–17:00] Setting up your environment".
            If a chunk summary has no such range, just use a plain heading.
        (4) "## Words you'll keep seeing" — 6-10 terms with one-line plain
            definitions.
        (5) "## Check yourself" — 5 questions testing whether the reader
            grasped the core ideas. Recall-level is fine here.
    """,
    "medium": """
        Produce study notes in Markdown with this structure:

        (1) "## TL;DR" — 2-3 sentences using standard terminology.
        (2) H2 sections mirroring the source's topics. Under each: concise
            bullets covering both what something does and why it works that
            way. Name specific tools, versions, and alternatives wherever
            the source does.
            If the chunk summaries below begin with a bracketed time range like
            [MM:SS–MM:SS], start this section's heading with that exact same
            bracketed range. If a chunk summary has no such range, just use a
            plain heading.
        (3) "## When this matters" — the practical situations where these
            ideas change what someone would actually do.
        (4) "## Key terms" — 5-8 terms, one line each. Skip anything a
            reader at this level already knows.
        (5) "## Check yourself" — 4 questions requiring application rather
            than recall.
    """,
    "expert": """
        Produce reference notes in Markdown with this structure:

        (1) "## Summary" — 2-3 dense sentences. Precise, no warm-up.
        (2) H2 sections mirroring the source's topics. Under each: bullets
            carrying specific detail — exact names, versions, numbers,
            mechanisms. One precise sentence beats three vague ones.
            If the chunk summaries below begin with a bracketed time range like
            [MM:SS–MM:SS], start this section's heading with that exact same
            bracketed range. If a chunk summary has no such range, just use a
            plain heading.
        (3) "## Trade-offs and edge cases" — where this breaks down, what it
            costs, where practice or the literature disagrees. This section
            is the point of the document; give it real weight.
        (4) "## Open questions" — what the source left unresolved, ambiguous,
            or unaddressed. Omit this section only if there is genuinely
            nothing.
        (5) "## Check yourself" — 3 questions probing boundaries and
            trade-offs, not definitions.

        Do NOT include a glossary. Defining standard terminology for this
        reader is padding.
    """,
}


# ---------------------------------------------------------------------------
# Question calibration — shapes the questions themselves, not prose style
# ---------------------------------------------------------------------------

_QUESTION_MODES = {
    "beginner": (
        "Write recall and recognition questions covering the core "
        "definitions and the main idea. Keep the wording plain. Wrong "
        "multiple-choice options should be clearly wrong, not subtle traps."
    ),
    "medium": (
        "Write questions that require applying a concept to a situation, "
        "not just recalling it. Wrong options should be plausible — common "
        "misconceptions make the best distractors."
    ),
    "expert": (
        "Write questions probing edge cases, trade-offs, and the boundaries "
        "of when something applies. Favour scenarios with a non-obvious "
        "correct answer. Distractors should be defensible-sounding and fail "
        "only on a specific detail."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_learning_mode(mode: Optional[str]) -> str:
    """Coerce anything unrecognized to the default, so a bad value from the
    client or an old DB row can never break a request."""
    if not mode:
        return DEFAULT_LEARNING_MODE
    mode = mode.strip().lower()
    return mode if mode in _MODES else DEFAULT_LEARNING_MODE


def apply_learning_mode(system_prompt: str, mode: Optional[str]) -> str:
    """Append the mode's prose-style instructions to a system prompt.

    The mode block goes last on purpose: it should override the generic
    style guidance in the base prompt where the two conflict.
    """
    return f"{system_prompt.strip()}\n\n{_MODES[normalize_learning_mode(mode)].strip()}"


def notes_structure(mode: Optional[str]) -> str:
    """The Markdown skeleton for generated notes. Goes in the USER prompt of
    build_notes(), not the system prompt — the model follows an explicit
    output shape far more reliably than a described one."""
    return textwrap.dedent(_NOTE_STRUCTURES[normalize_learning_mode(mode)]).strip()

def notes_opening_structure(mode: Optional[str]) -> str:
    """Instructions for writing JUST the opening block(s) of a timed
    (video/audio) note -- used by write_opening() in main.py's Path B
    pipeline, given the already-finalized per-chunk sections as context.
    Not used by the untimed pipeline, which still uses notes_structure()."""
    return textwrap.dedent(_OPENING_STRUCTURES[normalize_learning_mode(mode)]).strip()


def notes_section_structure(mode: Optional[str]) -> str:
    """Instructions for writing ONE final, ready-to-render H2 section
    directly from one chunk's raw transcript text -- used by write_section()
    in main.py. The caller supplies the exact time range and chunk text;
    this skeleton only describes HOW to write the section's content and
    heading format."""
    return textwrap.dedent(_SECTION_STRUCTURES[normalize_learning_mode(mode)]).strip()


def notes_closing_structure(mode: Optional[str]) -> str:
    """Instructions for writing the whole-document closing block(s)
    (glossary/check-yourself/trade-offs/etc.) -- used by write_closing() in
    main.py, given the already-finalized sections as context."""
    return textwrap.dedent(_CLOSING_STRUCTURES[normalize_learning_mode(mode)]).strip()


def question_style(mode: Optional[str]) -> str:
    """Difficulty calibration for practice questions.

    Deliberately not the same text as apply_learning_mode() — instructions
    like "lead with a concrete example" are advice for writing explanations
    and make no sense applied to quiz items.
    """
    return _QUESTION_MODES[normalize_learning_mode(mode)]