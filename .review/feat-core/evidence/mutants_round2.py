"""Mutation checks for round-2 fixes. Each mutant must make at least one test fail."""
import subprocess, sys, pathlib
ROOT = pathlib.Path.cwd()
MUTANTS = {
    "inflate without +1": ("src/linkx/fetch.py", "room - len(out) + 1", "room - len(out)"),
    "sink closed outside the Ctrl-C span": ("src/linkx/pipeline.py",
        "            summary = self._sink.close(interrupted=self.interrupted)\n        return",
        "        summary = self._sink.close(interrupted=self.interrupted)\n        return"),
    "httpx follows redirects (reads 3xx bodies)": ("src/linkx/fetch.py", "if response.next_request is None:", "if True:"),
    "CLI reports the first leaf": ("src/linkx/cli.py", "if not isinstance(leaf, OutputFailed):", "if True:"),
    "charset fallback catches LookupError only": ("src/linkx/fetch.py", "except (LookupError, ValueError):\n        return body.decode", "except LookupError:\n        return body.decode"),
}
failed_to_kill = 0
for name, (path, old, new) in MUTANTS.items():
    file = ROOT / path
    source = file.read_bytes().decode("utf-8")
    assert old in source, f"{name}: pattern not found"
    file.write_bytes(source.replace(old, new, 1).encode("utf-8"))
    try:
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                                capture_output=True, text=True)
    finally:
        file.write_bytes(source.encode("utf-8"))
    killed = result.returncode != 0
    failed_to_kill += not killed
    last = [l for l in result.stdout.splitlines() if l.startswith("FAILED")][:1]
    print(f"{'KILLED' if killed else 'SURVIVED'}  {name}  {last[0] if last else ''}")
print(f"{len(MUTANTS) - failed_to_kill}/{len(MUTANTS)} mutants killed")
sys.exit(1 if failed_to_kill else 0)
