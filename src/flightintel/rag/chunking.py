"""Heading-aware markdown chunking for the RAG corpus (PHASE2_PLAN Q3).

The measured design: a file that fits the cap becomes ONE chunk (every ADR
does, so options and decision travel together); an oversized file splits at
the shallowest heading level and recurses; a leaf section still over the cap
splits at paragraph boundaries with greedy packing; pieces under the floor
merge into a neighbor so heading stubs never become index noise.

ATX headings only ('# ...'), which is all the corpus uses. Headings inside
``` code fences are not headings. Pure functions, no I/O, no LLM.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

CAP_CHARS = 6000
FLOOR_CHARS = 200


class Chunk(BaseModel):
    breadcrumb: str
    heading: str
    text: str
    part: int | None = None


@dataclass
class _Node:
    level: int
    title: str
    lines: list[str] = field(default_factory=list)
    children: list["_Node"] = field(default_factory=list)


def _parse(text: str) -> _Node:
    """Build a section tree: each node owns its heading line and body lines
    up to its first child heading."""
    root = _Node(level=0, title="")
    stack = [root]
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        level = 0
        if not fence and line.startswith("#"):
            n = len(line) - len(line.lstrip("#"))
            if 1 <= n <= 6 and line[n : n + 1] == " ":
                level = n
        if level:
            node = _Node(level=level, title=line[level + 1 :].strip(), lines=[line])
            while stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(node)
            stack.append(node)
        else:
            stack[-1].lines.append(line)
    return root


def _subtree_text(node: _Node) -> str:
    parts = ["\n".join(node.lines)]
    parts.extend(_subtree_text(c) for c in node.children)
    return "\n".join(p for p in parts if p).strip("\n")


def _doc_title(root: _Node, fallback: str) -> str:
    for child in root.children:
        if child.level == 1:
            return child.title
    return fallback


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines outside code fences."""
    paras: list[str] = []
    cur: list[str] = []
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if not line.strip() and not fence:
            if cur:
                paras.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        paras.append("\n".join(cur))
    return paras


def _bold_label(para: str) -> str | None:
    """A paragraph whose first line opens with a closed bold run
    (**Q3. ...**) is a run-in heading; when an oversized leaf section
    splits, it starts its own chunk and the label becomes the heading,
    so the chunk id names the exact question (PHASE2_PLAN Q14)."""
    first = para.splitlines()[0].strip()
    if not first.startswith("**"):
        return None
    end = first.find("**", 2)
    if end <= 2:
        return None
    return first[2:end].strip()


def _pack_greedy(
    paras: list[str], breadcrumb: str, heading: str, cap: int
) -> list[Chunk]:
    """Greedy-pack paragraphs under the cap. A lone paragraph over the cap
    stays one oversized part rather than being cut mid-sentence; the index
    build asserts real token limits and would fail loudly there (Q4)."""
    groups: list[str] = []
    cur = ""
    for para in paras:
        candidate = f"{cur}\n\n{para}" if cur else para
        if cur and len(candidate) > cap:
            groups.append(cur)
            cur = para
        else:
            cur = candidate
    if cur:
        groups.append(cur)
    if len(groups) == 1:
        return [Chunk(breadcrumb=breadcrumb, heading=heading, text=groups[0])]
    return [
        Chunk(breadcrumb=breadcrumb, heading=heading, text=g, part=i)
        for i, g in enumerate(groups, start=1)
    ]


def _pack_paragraphs(text: str, breadcrumb: str, heading: str, cap: int) -> list[Chunk]:
    """Fallback for an oversized leaf section: bold-label paragraphs each
    start a new chunk carrying the label as heading (corpus v2, Q14);
    runs between labels greedy-pack under the cap as before."""
    segments: list[tuple[str | None, list[str]]] = []
    for para in _split_paragraphs(text):
        label = _bold_label(para)
        if label is not None or not segments:
            segments.append((label, [para]))
        else:
            segments[-1][1].append(para)
    chunks: list[Chunk] = []
    for label, paras in segments:
        if label is None:
            chunks.extend(_pack_greedy(paras, breadcrumb, heading, cap))
        else:
            chunks.extend(
                _pack_greedy(paras, f"{breadcrumb} > {label}", label, cap)
            )
    return chunks


def _emit(node: _Node, crumbs: list[str], doc_title: str, cap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    own = "\n".join(node.lines).strip("\n")
    if own:
        breadcrumb = " > ".join(crumbs)
        heading = crumbs[-1]
        if len(own) <= cap:
            chunks.append(Chunk(breadcrumb=breadcrumb, heading=heading, text=own))
        else:
            chunks.extend(_pack_paragraphs(own, breadcrumb, heading, cap))
    for child in node.children:
        child_crumbs = (
            crumbs
            if child.level == 1 and child.title == doc_title
            else crumbs + [child.title]
        )
        subtree = _subtree_text(child)
        if len(subtree) <= cap:
            chunks.append(
                Chunk(
                    breadcrumb=" > ".join(child_crumbs),
                    heading=child_crumbs[-1],
                    text=subtree,
                )
            )
        else:
            chunks.extend(_emit(child, child_crumbs, doc_title, cap))
    return chunks


def _merge_floor(chunks: list[Chunk], floor: int) -> list[Chunk]:
    """Merge sub-floor stubs into the following chunk (previous, if last).
    The stub's heading line stays inside the merged text; the breadcrumb of
    the substantial chunk wins."""
    out: list[Chunk] = []
    pending = ""
    for ch in chunks:
        if len(ch.text) < floor:
            pending = f"{pending}\n\n{ch.text}" if pending else ch.text
            continue
        if pending:
            ch = ch.model_copy(update={"text": f"{pending}\n\n{ch.text}"})
            pending = ""
        out.append(ch)
    if pending:
        if out:
            out[-1] = out[-1].model_copy(
                update={"text": f"{out[-1].text}\n\n{pending}"}
            )
        else:
            out.append(chunks[0].model_copy(update={"text": pending}))
    return out


def chunk_markdown(
    text: str,
    fallback_title: str,
    cap: int = CAP_CHARS,
    floor: int = FLOOR_CHARS,
) -> list[Chunk]:
    stripped = text.strip("\n")
    if not stripped:
        return []
    root = _parse(stripped)
    title = _doc_title(root, fallback_title)
    if len(stripped) <= cap:
        return [Chunk(breadcrumb=title, heading=title, text=stripped)]
    root.title = title
    chunks = _emit(root, [title], title, cap)
    return _merge_floor(chunks, floor)
