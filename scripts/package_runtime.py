"""Build the AgentCore deployment ZIP. Linux ARM64, Python 3.12. No upload.

Dependencies are installed into a clean temporary directory for the target
platform — not copied from the local macOS virtualenv, whose psycopg and
pydantic binaries would not load on AgentCore. The local venv is Python
3.12.11 precisely so that what is tested here matches what runs there.
"""
from __future__ import annotations
import shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "ramco-runtime.zip"
SRC = ROOT / "src" / "pilot"
ENTRY = ROOT / "runtime.py"


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ramco-package-") as tmp:
        pkg = Path(tmp)
        subprocess.run([
            sys.executable, "-m", "pip", "install",
            "--platform", "manylinux2014_aarch64", "--implementation", "cp",
            "--python-version", "3.12", "--abi", "cp312",
            "--only-binary=:all:", "--no-compile",
            "--target", str(pkg),
            "-r", str(ROOT / "requirements-agentcore.txt"),
        ], check=True)

        shutil.copy2(ENTRY, pkg / "runtime.py")
        shutil.copytree(SRC, pkg / "src" / "pilot")
        # The data room is NOT packaged. The runtime reads everything it
        # needs from the two databases; shipping customer tickets inside a
        # deployment artifact would put case data somewhere it does not
        # belong, and the brief is explicit about that.
        for junk in pkg.rglob("__pycache__"):
            shutil.rmtree(junk, ignore_errors=True)

        with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
            for f in pkg.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(pkg))

    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
        assert z.testzip() is None
        assert "runtime.py" in names, "entrypoint must sit at the ZIP root"
        assert "src/pilot/pipeline.py" in names
        bad = [n for n in names
               if n.endswith(".env") or "tickets_export" in n
               or n.endswith(".sqlite3") or "/.aws/" in n]
        assert not bad, f"artifact contains things it must not: {bad[:5]}"

    size = OUT.stat().st_size / 1024 ** 2
    print(f"built {OUT} ({size:.1f} MB), {len(names)} entries")
    print("Python 3.12 / Linux ARM64. No case data, no secrets. Nothing uploaded.")
    if size > 250:
        raise SystemExit("ZIP exceeds the 250 MB compressed limit")


if __name__ == "__main__":
    main()
