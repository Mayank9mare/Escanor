"""
research/ingest.py

Bringing a paper or article into the second brain so Escanor can explain
it (with the learn-anything skill) and you can search it later.

Accepted sources:
- an arXiv id or link ("2401.12345", "arxiv.org/abs/2401.12345v2") ->
  the PDF from arxiv.org
- any http(s) link -> HTML as text, or a PDF (through web.py's address
  checks: no localhost / private network)
- a local PDF/text file, only inside allowed folders (default
  ~/Downloads, ~/Documents, ~/Desktop) -- the model picks the path, and
  it must not be able to read ~/.ssh

The text is saved to research/<slug>/source.md with a small header
(title, source, date). Its content came from outside, so the tool's
output is untrusted (fenced) like web pages.
"""
from __future__ import annotations
import io
import re
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from ..assistant.web import BlockedURL, _CheckingRedirects, _resolve, check_url, fetch_text
from ..brain.vault import Vault

MAX_PDF_BYTES = 40_000_000
_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?$", re.I)


@dataclass
class Document:
    title: str
    source: str
    text: str


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "untitled"


def pdf_text(data: bytes) -> tuple[str, str]:
    """(title, text) from PDF bytes."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("Install the research extra (`uv pip install -e '.[research]'`) to read PDFs.") from e
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"<!-- page {number} -->\n{text}")
    title = ""
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip()
    if not title and pages:
        title = next((line.strip() for line in pages[0].splitlines()[1:] if len(line.strip()) > 10), "")
    return title, "\n\n".join(pages)


def _download(url: str, allow_private: bool, opener=None, timeout: float = 30) -> tuple[bytes, str]:
    check_url(url, allow_private, _resolve)
    opener = opener or urllib.request.build_opener(_CheckingRedirects(allow_private, _resolve))
    request = urllib.request.Request(url, headers={"User-Agent": "Escanor/0.1 (research reader)"})
    with opener.open(request, timeout=timeout) as response:
        data = response.read(MAX_PDF_BYTES + 1)
        content_type = response.headers.get("Content-Type", "").lower()
    if len(data) > MAX_PDF_BYTES:
        raise RuntimeError("That file is larger than 40 MB.")
    return data, content_type


def arxiv_title(paper: str, download: Callable[[str, bool], tuple[bytes, str]]) -> str:
    """The paper's title from arXiv's metadata API ('' if unavailable). The
    PDF's first line is often a licence notice, not the title (B14)."""
    import xml.etree.ElementTree as ET
    try:
        data, _ = download(f"https://export.arxiv.org/api/query?id_list={paper}", False)
        feed = ET.fromstring(data)
    except (RuntimeError, OSError, ET.ParseError, BlockedURL):
        return ""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    title = feed.find("a:entry/a:title", ns)
    return " ".join(title.text.split()) if title is not None and title.text else ""


class Ingestor:
    def __init__(self, vault: Vault, allowed_dirs: list[Path], allow_private: bool = False,
                 download: Callable[[str, bool], tuple[bytes, str]] | None = None,
                 fetch_page: Callable[..., tuple[str, str, str]] = fetch_text,
                 today: Callable[[], date] = date.today):
        self.vault = vault
        self.allowed_dirs = [d.expanduser().resolve() for d in allowed_dirs]
        self.allow_private = allow_private
        self._download = download or (lambda url, private: _download(url, private))
        self._fetch_page = fetch_page
        self._today = today

    def load(self, source: str) -> Document:
        source = source.strip()
        arxiv = _ARXIV.search(source)
        if arxiv and ("arxiv" in source.lower() or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", source)):
            paper = arxiv.group(1) + (arxiv.group(2) or "")
            data, _ = self._download(f"https://arxiv.org/pdf/{paper}", self.allow_private)
            pdf_title, text = pdf_text(data)
            title = arxiv_title(paper, self._download) or pdf_title or f"arXiv {paper}"
            return Document(title, f"https://arxiv.org/abs/{paper}", text)
        if source.startswith(("http://", "https://")):
            if source.lower().split("?")[0].endswith(".pdf"):
                data, _ = self._download(source, self.allow_private)
                title, text = pdf_text(data)
                return Document(title or Path(source.split("?")[0]).stem, source, text)
            try:
                final_url, title, text = self._fetch_page(source, allow_private=self.allow_private)
            except RuntimeError as e:
                if "PDF" not in str(e):
                    raise
                data, _ = self._download(source, self.allow_private)
                title, text = pdf_text(data)
                final_url = source
            return Document(title or final_url, final_url, text)
        return self._load_file(Path(source).expanduser())

    def _load_file(self, path: Path) -> Document:
        resolved = path.resolve()
        if not any(resolved.is_relative_to(d) for d in self.allowed_dirs):
            allowed = ", ".join(str(d) for d in self.allowed_dirs)
            raise PermissionError(f"Can only read files in: {allowed}.")
        if not resolved.is_file():
            raise FileNotFoundError(f"No file at {resolved}.")
        if resolved.suffix.lower() == ".pdf":
            title, text = pdf_text(resolved.read_bytes())
        elif resolved.suffix.lower() in (".md", ".txt", ".html", ".htm"):
            title, text = "", resolved.read_text(encoding="utf-8", errors="replace")
        else:
            raise ValueError("Only PDF, Markdown, text or HTML files.")
        return Document(title or resolved.stem, str(resolved), text)

    def ingest(self, source: str) -> tuple[str, Document]:
        """Load and save. Returns (note path, document)."""
        document = self.load(source)
        if not document.text.strip():
            raise ValueError("No readable text found (a scanned PDF needs OCR first).")
        slug = slugify(document.title)
        header = (f"---\ntitle: {document.title!r}\nsource: {document.source}\nsaved: {self._today().isoformat()}\n---\n\n"
                  f"# {document.title}\n\nSource: {document.source}\n\n")
        path, _ = self.vault.write(f"research/{slug}/source.md", header + document.text, overwrite=True)
        return path, document

