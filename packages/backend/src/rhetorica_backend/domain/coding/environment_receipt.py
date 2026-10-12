"""Package trusted preparation evidence as an artifact, separate from the candidate."""

import io
import json
import tarfile
from typing import Any


def python_environment_archive(receipt: dict[str, Any]) -> bytes:
    content = json.dumps(receipt, sort_keys=True).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        entry = tarfile.TarInfo("python-environment.json")
        entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))
    return output.getvalue()
