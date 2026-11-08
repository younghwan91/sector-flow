#!/usr/bin/env python
"""문서의 코드 인용(`파일:줄`)을 검사하고, 코드가 움직였으면 다시 맞춘다.

    uv run python scripts/docs_refs.py          # 검사만 — 어긋나면 1
    uv run python scripts/docs_refs.py --fix    # 옮길 수 있는 것을 옮기고 남은 것을 보고한다

**인용 규약.** 인용은 코드 스팬 `` `path:N` `` 또는 `` `path:N-M` `` 이다. 인용 바로 앞(사이에 공백만)의
`` `기호` `` 나 바로 뒤의 `` (`기호`) `` 가 **짝 지은 기호**다 — 스팬 맨 앞 식별자의 마지막 성분
(`EOK = 1e8` → `EOK`, `State._leads` → `_leads`). 스팬이 `def`·`class` 로 시작하면(`` `def load` ``) 그
정의 줄이 있어야 한다. 식별자로 시작하지 않는 스팬(열 이름 등)은 짝이 아니다. 검사는 모든 인용이 있는
파일·줄 범위를 가리키는지, 짝 지은 기호가 그 줄 ±3 안에 있는지 본다(git 없이 — CI 는 얕은 체크아웃이다).

**기준 커밋.** 인용이 있는 문서는 ``<!-- refs-verified-at: <sha> -->`` 한 줄을 갖는다. 그 커밋의 소스에
대해 인용이 맞다는 표시이고 ``--fix`` 만 쓰고 고친다. 문서를 마지막으로 고친 커밋을 기준으로 삼으면 오타만
고친 커밋·리베이스가 기준을 조용히 바꾼다 — 그래서 따로 적는다.

**--fix**

1. git 대응 — 기준 커밋의 소스와 지금 소스를 diff 해 줄을 옮긴다. 기준 표시가 들어온 커밋의 문서에 그대로
   있던 줄의 인용만 옮긴다(그 뒤에 쓴 인용은 지금 코드를 보고 쓴 것이다). 짝 없는 줄 인용도 이걸로 따라간다.
2. 짝 지은 기호가 그래도 ±3 밖이면 — 기준 소스에서 그 기호가 있던 줄과 **내용이 같은** 줄을 지금 파일에서
   찾는다. 하나뿐이면 그만큼 옮기고, 여럿·없음이면 추측하지 않고 보고한다. 기준 소스가 없을 때만 기호로
   찾는다(하나뿐이거나 `def`·`class` 스팬의 정의 줄).
3. 남은 문제가 없고 인용한 파일이 HEAD 와 같으면 기준 표시를 HEAD 로 바꾼다. 커밋 안 한 변경이 있으면 그대로
   두고 알린다 — 커밋 뒤 다시 돌린다.
"""
from __future__ import annotations

import argparse
import difflib
import keyword
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("docs/formulas.md", "README.md", "docs/trading-guide.md")
SLACK = 3
FIX_HINT = "uv run python scripts/docs_refs.py --fix"

_PATH = r"(?:scripts|src|tests|research|deploy)/[A-Za-z0-9_./-]+"
REF = re.compile(rf"`({_PATH}):(\d+)(?:-(\d+))?`")
STAMP = re.compile(r"<!-- refs-verified-at: ([0-9a-f]{7,40}) -->")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_BEFORE = re.compile(r"`([^`\n]+)`\s*$")
_AFTER = re.compile(r"^\s*\(`([^`\n]+)`")


@dataclass
class Ref:
    line: int           # 문서의 줄(1-기반)
    start: int          # 그 줄 안에서 인용 스팬의 시작·끝(문자)
    end: int
    path: str
    n1: int
    n2: int
    sym: str | None     # 짝 지은 기호
    defn: bool          # `def X`·`class X` 꼴 — 정의 줄이어야 한다
    ranged: bool        # `N-M` 꼴로 적혔나

    def span(self, n1: int | None = None, n2: int | None = None) -> str:
        a, b = (self.n1 if n1 is None else n1), (self.n2 if n2 is None else n2)
        return f"`{self.path}:{a}" + ("" if a == b and not self.ranged else f"-{b}") + "`"


def _symbol(span: str) -> tuple[str, bool] | None:
    """코드 스팬 → (기호, 정의 꼴인가). 앞의 키워드는 건너뛴다(`def load(path)` → `load`)."""
    if REF.fullmatch(f"`{span}`"):
        return None
    s, defn = span, False
    while (m := _IDENT.match(s)):
        w = m.group(0)
        if keyword.iskeyword(w) or w == "async":
            defn = defn or w in ("def", "class")
            s = s[m.end():].lstrip()
            continue
        return w.rsplit(".", 1)[-1], defn
    return None


def refs(text: str) -> list[Ref]:
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in REF.finditer(line):
            n1 = int(m.group(2))
            n2 = int(m.group(3)) if m.group(3) else n1
            got = None
            after = _AFTER.match(line[m.end():])
            before = _BEFORE.search(line[:m.start()])
            if after:
                got = _symbol(after.group(1))
            if got is None and before:
                got = _symbol(before.group(1))
            sym, defn = got if got else (None, False)
            out.append(Ref(lineno, m.start(), m.end(), m.group(1), n1, n2, sym, defn,
                           m.group(3) is not None))
    return out


def _sym_re(sym: str, defn: bool) -> re.Pattern:
    e = re.escape(sym)
    return re.compile(rf"\b(?:def|class)\s+{e}\b" if defn else rf"\b{e}\b")


def _has(lines: list[str], r: Ref, n1: int, n2: int) -> bool:
    lo, hi = max(0, n1 - 1 - SLACK), min(len(lines), n2 + SLACK)
    pat = _sym_re(r.sym, r.defn)
    return any(pat.search(ln) for ln in lines[lo:hi])


def problem(root: Path, r: Ref) -> str | None:
    f = root / r.path
    if not f.is_file():
        return "파일이 없다"
    lines = f.read_text(encoding="utf-8").splitlines()
    if not (1 <= r.n1 <= r.n2 <= len(lines)):
        return f"줄 범위 밖(파일 {len(lines)}줄)"
    if r.sym and not _has(lines, r, r.n1, r.n2):
        return f"`{'def ' if r.defn else ''}{r.sym}` 가 ±{SLACK}줄 안에 없다"
    return None


def check(root: Path = ROOT, docs=DOCS) -> list[str]:
    bad = []
    for doc in docs:
        f = root / doc
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        rs = refs(text)
        if rs and len(STAMP.findall(text)) != 1:
            bad.append(f"{doc}: 기준 표시 `<!-- refs-verified-at: <sha> -->` 가 한 줄 있어야 한다")
        for r in rs:
            if (p := problem(root, r)):
                bad.append(f"{doc}:{r.line} {r.span()}: {p}")
    return bad


# ── --fix ───────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def _line_map(old: list[str], new: list[str]) -> dict[int, int]:
    """옛 줄 → 새 줄. 같은 줄로 남은 것만(바뀐·지운 줄은 없다)."""
    m = {}
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, _j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                m[i1 + k + 1] = j1 + k + 1
    return m


def _nearest_mapped(m: dict[int, int], n: int) -> int | None:
    """n 이 바뀐 줄이면 가장 가까운 앞쪽 같은 줄에서 같은 거리만큼."""
    for d in range(0, 50):
        if n - d in m:
            return m[n - d] + d
    return None


def _by_content(old: list[str], cur: list[str], r: Ref) -> tuple[int, int] | str:
    """기준 소스에서 기호가 있던 줄과 내용이 같은 줄을 지금 파일에서 찾아 그만큼 옮긴다."""
    pat = _sym_re(r.sym, r.defn)
    lo, hi = max(1, r.n1 - SLACK), min(len(old), r.n2 + SLACK)
    near = sorted((i for i in range(lo, hi + 1) if pat.search(old[i - 1])),
                  key=lambda i: (not (r.n1 <= i <= r.n2), abs(i - r.n1)))
    if not near:
        return f"기준 소스에서도 `{r.sym}` 가 그 자리에 없다 — 손으로 고친다"
    a0 = near[0]
    want = old[a0 - 1].strip()
    hits = [i + 1 for i, ln in enumerate(cur) if ln.strip() == want]
    if len(hits) != 1:
        return (f"`{r.sym}` 가 있던 줄과 같은 줄이 지금 {len(hits)}곳이다"
                f"({'모호' if hits else '바뀜'}) — 손으로 고친다")
    d = hits[0] - a0
    return r.n1 + d, r.n2 + d


def _by_symbol(cur: list[str], r: Ref) -> tuple[int, int] | str:
    """기준 소스가 없을 때만 — 기호가 하나뿐이거나 정의 꼴이면 그 줄로."""
    pat = _sym_re(r.sym, r.defn)
    hits = [i + 1 for i, ln in enumerate(cur) if pat.search(ln)]
    if not hits:
        return f"`{r.sym}` 가 파일에 없다"
    if len(hits) > 1:
        return f"`{r.sym}` 가 {len(hits)}곳에 있어 모호하다(기준 소스 없음) — 손으로 고친다"
    return hits[0], hits[0] + (r.n2 - r.n1)


def _stamp_commit(root: Path, doc: str, sha: str) -> str | None:
    """기준 표시가 지금 값으로 들어온 커밋. 아직 커밋 안 했으면 None."""
    out = _git(root, "log", "-1", "--format=%H", "-S", f"refs-verified-at: {sha}", "--", doc)
    return (out or "").strip() or None


def fix(root: Path = ROOT, docs=DOCS, base: str | None = None,
        base_tree: Path | None = None) -> tuple[int, list[str], list[str]]:
    """문서를 제자리에서 고친다. (옮긴 인용 수, 남은 문제, 알림)을 돌려준다.

    ``base_tree`` 는 검사용 — 기준 소스·문서를 git 대신 그 폴더에서 읽고 기준 표시는 안 바꾼다.
    """
    changed, left, notes = 0, [], []
    for doc in docs:
        f = root / doc
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        all_refs = refs(text)
        if not all_refs:
            continue
        stamp = STAMP.search(text)
        rev = base or (stamp.group(1) if stamp else None)

        def old_of(path: str) -> str | None:
            if base_tree is not None:
                p = base_tree / path
                return p.read_text(encoding="utf-8") if p.is_file() else None
            return _git(root, "show", f"{rev}:{path}") if rev else None

        if base_tree is not None:
            base_doc = old_of(doc)
        elif rev and (c := _stamp_commit(root, doc, rev)):
            base_doc = _git(root, "show", f"{c}:{doc}")
        else:
            base_doc = text                         # 기준 표시를 아직 커밋 안 했다 — 지금 문서가 그 판이다
        if rev and base_tree is None and _git(root, "cat-file", "-e", f"{rev}^{{commit}}") is None:
            notes.append(f"{doc}: 기준 커밋 {rev[:12]} 이 없다(리베이스·스쿼시?) — 짝 인용만 기호로 옮기고, "
                         "줄만 적은 인용은 범위만 봤다")
        in_base = set((base_doc or "").split("\n"))
        doc_lines = text.split("\n")
        orig = list(doc_lines)
        cache: dict[str, tuple[list[str], list[str] | None, dict[int, int] | None]] = {}
        unmapped = 0
        for r in reversed(all_refs):               # 뒤에서부터 — 같은 줄 안의 위치가 안 밀린다
            if r.path not in cache:
                p = root / r.path
                cur = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
                o = old_of(r.path)
                old = o.splitlines() if o is not None else None
                cache[r.path] = (cur, old, _line_map(old, cur) if old is not None and cur else None)
            cur, old, m = cache[r.path]
            if not cur:
                left.append(f"{doc}:{r.line} {r.span()}: 파일이 없다")
                continue
            n1, n2 = r.n1, r.n2
            mapped = m is not None and orig[r.line - 1] in in_base
            if mapped:
                a, b = _nearest_mapped(m, r.n1), _nearest_mapped(m, r.n2)
                if a is not None and b is not None and a <= b:
                    n1, n2 = a, b
            elif r.sym is None:
                unmapped += 1
            if r.sym and not (1 <= n1 <= n2 <= len(cur) and _has(cur, r, n1, n2)):
                got = _by_content(old, cur, r) if (old is not None and mapped) else _by_symbol(cur, r)
                if isinstance(got, str):
                    left.append(f"{doc}:{r.line} {r.span()}: {got}")
                    continue
                n1, n2 = got
            if not (1 <= n1 <= n2 <= len(cur)):
                left.append(f"{doc}:{r.line} {r.span()}: 줄 범위 밖이고 옮길 근거가 없다")
                continue
            if (n1, n2) != (r.n1, r.n2):
                ln = doc_lines[r.line - 1]
                doc_lines[r.line - 1] = ln[:r.start] + r.span(n1, n2) + ln[r.end:]
                changed += 1
        if unmapped:
            notes.append(f"{doc}: 줄만 적은 인용 {unmapped}개는 기준 소스와 대응할 근거가 없어 범위만 봤다")
        if base_tree is None and not left:
            head = (_git(root, "rev-parse", "HEAD") or "").strip()
            paths = sorted({r.path for r in all_refs})
            dirty = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", *paths]).returncode
            if not head:
                notes.append(f"{doc}: git 이 없어 기준 표시를 못 적었다")
            elif dirty:
                notes.append(f"{doc}: 인용한 파일에 커밋 안 한 변경이 있어 기준 표시를 그대로 뒀다 — "
                             f"커밋한 뒤 `{FIX_HINT}` 를 다시 돌린다")
            else:
                new = f"<!-- refs-verified-at: {head} -->"
                body = "\n".join(doc_lines)
                body = STAMP.sub(new, body, count=1) if STAMP.search(body) else _add_stamp(body, new)
                doc_lines = body.split("\n")
        f.write_text("\n".join(doc_lines), encoding="utf-8")
    return changed, left, notes


def _add_stamp(body: str, stamp: str) -> str:
    """첫 제목 줄 바로 아래에 넣는다."""
    lines = body.split("\n")
    i = next((k + 1 for k, ln in enumerate(lines) if ln.startswith("# ")), 0)
    return "\n".join(lines[:i] + [stamp] + lines[i:])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fix", action="store_true", help="어긋난 인용을 옮기고 기준 표시를 갱신한다")
    ap.add_argument("--base", default=None, help="기준 커밋을 덮어쓴다(기본: 문서의 refs-verified-at)")
    ap.add_argument("docs", nargs="*", default=list(DOCS))
    a = ap.parse_args(argv)
    if a.fix:
        n, left, notes = fix(ROOT, a.docs, a.base)
        print(f"옮긴 인용 {n}개")
        for x in notes:
            print("  알림 —", x)
        for x in left:
            print("  남음 —", x)
        left = check(ROOT, a.docs) or left
    else:
        left = check(ROOT, a.docs)
        for x in left:
            print(x)
        if left:
            print(f"\n고치려면: {FIX_HINT}")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
