"""Mutation checks for fix set 3. Each mutant must make at least one test fail."""
import pathlib, subprocess, sys
ROOT = pathlib.Path.cwd()
MUTANTS = {
    "peer check fails open again": ("src/linkx/fetch.py", "        if not peer:\n", "        if False:\n"),
    "3xx responses not closed": ("src/linkx/fetch.py", "            finally:\n                await response.aclose()\n", "            finally:\n                pass\n"),
    "InvalidURL not mapped": ("src/linkx/fetch.py", "except httpx.InvalidURL as exc:  # a redirect", "except ZeroDivisionError as exc:  # a redirect"),
    "no upper bound": ("src/linkx/cli.py", "if value > maximum:", "if False:"),
}
survived = 0
for name, (path, old, new) in MUTANTS.items():
    file = ROOT / path
    source = file.read_bytes().decode("utf-8")
    assert old in source, f"{name}: pattern not found"
    file.write_bytes(source.replace(old, new, 1).encode("utf-8"))
    try:
        cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--timeout-method=thread"] if False else [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
        if name == "no upper bound":  # without bounds the bound tests hang; run only a safe one
            cmd += ["tests/test_cli.py::test_options_have_an_upper_bound"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        killed = result.returncode != 0
        first = next((l for l in result.stdout.splitlines() if l.startswith("FAILED")), "")
    except subprocess.TimeoutExpired:
        killed, first = True, "(test run hung: the mutant made a test wait forever)"
    finally:
        file.write_bytes(source.encode("utf-8"))
    survived += not killed
    print(f"{'KILLED' if killed else 'SURVIVED'}  {name}  {first}")
print(f"{len(MUTANTS) - survived}/{len(MUTANTS)} mutants killed")
sys.exit(1 if survived else 0)
