"""Build two self-contained submissions and verify Python code equivalence."""

import ast
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tokenize
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("warehouse", "tests", "tools", "docs", "launch", "resource")
FILES = (
    "README.md", "Dockerfile", "compose.yaml", ".dockerignore", ".gitignore",
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "package.xml", "тестовое задание.md", "доп. инфа.md",
)


class WithoutDocstrings(ast.NodeTransformer):
    def generic_visit(self, node):
        node = super().generic_visit(node)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    node.body.pop(0)
            if not node.body and not isinstance(node, ast.Module):
                node.body = [ast.Pass()]
        return node


def normalized(source):
    return ast.dump(WithoutDocstrings().visit(ast.parse(source)), include_attributes=False)


def strip_python(source):
    lines = source.splitlines(keepends=True)
    replacements = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        expr = node.body[0]
        if not isinstance(expr.value, ast.Constant) or not isinstance(expr.value.value, str):
            continue
        indent = len(lines[expr.lineno - 1]) - len(lines[expr.lineno - 1].lstrip())
        only_statement = len(node.body) == 1 and not isinstance(node, ast.Module)
        replacements.append((expr.lineno, expr.end_lineno, indent, only_statement))
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, col = token.start
            end_col = token.end[1]
            line = lines[row - 1]
            lines[row - 1] = line[:col] + " " * (end_col - col) + line[end_col:]
    for start, end, indent, only in replacements:
        for row in range(start - 1, end):
            lines[row] = "\n" if lines[row].endswith("\n") else ""
        if only:
            lines[start - 1] = " " * indent + "pass\n"
    cleaned = "\n".join(line.rstrip() for line in "".join(lines).splitlines()) + "\n"
    if normalized(source) != normalized(cleaned):
        raise ValueError("Removing documentation changed the Python AST")
    if any(t.type == tokenize.COMMENT for t in tokenize.generate_tokens(io.StringIO(cleaned).readline)):
        raise ValueError("A Python comment remains")
    return cleaned


def strip_comments(path, source):
    if path.suffix == ".py":
        return strip_python(source)
    if path.suffix == ".js":
        # This project's JS comments occupy entire lines. Removing just those
        # preserves template literals, nested expressions and websocket URLs.
        return "\n".join(line for line in source.splitlines()
                          if not line.lstrip().startswith("//")) + "\n"
    if path.suffix == ".css":
        return re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    if path.suffix in {".html", ".xml"}:
        return re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    if path.name == "Dockerfile":
        return "\n".join(line for line in source.splitlines()
                          if not line.lstrip().startswith("#")) + "\n"
    return source


def source_files():
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix not in {".pyc", ".pyo"})
    return sorted(paths)


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    manifest = {}
    sources = source_files()
    for variant in ("commented", "clean"):
        candidate = ROOT / "versions" / variant
        target = candidate.resolve()
        # Resolve and verify the exact generated target before recursive deletion.
        allowed = (ROOT / "versions").resolve()
        if (not allowed.is_relative_to(ROOT.resolve()) or target.parent != allowed
                or candidate.is_symlink() or target.name != variant):
            raise ValueError(f"Unsafe output directory: {target}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        checked = 0
        for path in sources:
            if path.is_symlink():
                raise ValueError(f"Unexpected source symlink: {path}")
            destination = target / path.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = path.read_text(encoding="utf-8")
            output = strip_comments(path, source) if variant == "clean" else source
            destination.write_text(output, encoding="utf-8", newline="\n")
            checked += int(variant == "clean" and path.suffix == ".py")
        archive = artifacts / f"dikom-{variant}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo(str(path.relative_to(target)).replace("\\", "/"))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    output.writestr(info, path.read_bytes())
        manifest[variant] = {"path": archive.relative_to(ROOT).as_posix(),
                             "bytes": archive.stat().st_size,
                             "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                             "files": len(sources), "python_ast_verified": checked}
    (artifacts / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
