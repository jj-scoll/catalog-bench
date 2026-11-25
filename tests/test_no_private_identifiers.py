"""Everything in this repository is public. Fail if a file that git would publish carries an AWS account ID,
resource ID, IAM identity or a local home path — the usual ways private infrastructure details leak into results."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
PRIVATE = re.compile(
    r"arn:aws[a-z-]*:[^:\s]*:[^:\s]*:\d{12}"  # any ARN carrying an account ID
    r"|[a-z]-\d{12}\b"  # account ID used as a name suffix
    r"|AWSReservedSSO"
    r"|\b(i|subnet|vpc|sg|vol|eni)-[0-9a-f]{8,17}\b"
    r"|/home/(?!nonroot/)[a-z]"  # a real user's home; /home/nonroot is a container path
)
SKIP = {"uv.lock", "tests/test_no_private_identifiers.py"}


def test_no_file_git_would_publish_contains_private_identifiers():
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    leaks = []
    for name in listed:
        path = ROOT / name
        if name in SKIP or not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        leaks += [f"{name}: {m.group(0)[:6]}..." for m in PRIVATE.finditer(text)]
    assert not leaks, f"{len(leaks)} private identifiers, first: {leaks[:5]}"
