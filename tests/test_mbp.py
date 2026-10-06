import io
import zipfile

import pytest

from adminbot.errors import ValidationError
from adminbot.userbots import missing_dependencies, validate_mbp

MAX = 1024 * 1024


def make_mbp(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


META = "maubot: 0.1.0\nid: xyz.prof.echo\nversion: 1.0\nmodules: [echo]\nmain_class: EchoBot\n"


def test_valid_mbp():
    info = validate_mbp(make_mbp({"maubot.yaml": META, "echo/__init__.py": ""}), MAX)
    assert (info.plugin_id, info.version, info.main_class) == ("xyz.prof.echo", "1.0", "EchoBot")
    assert len(info.sha256) == 64


def test_main_module_as_file_and_explicit_module():
    meta = META.replace("main_class: EchoBot", "main_class: other/Bot").replace("[echo]", "[echo, other]")
    validate_mbp(make_mbp({"maubot.yaml": meta, "echo.py": "", "other.py": ""}), MAX)


@pytest.mark.parametrize("files", [
    {"maubot.yaml": META, "echo/__init__.py": "", "../evil.py": ""},
    {"maubot.yaml": META, "echo/__init__.py": "", "/etc/evil": ""},
    {"echo/__init__.py": ""},
    {"maubot.yaml": META},
    {"maubot.yaml": META.replace("id: xyz.prof.echo", "id: Bad ID"), "echo/__init__.py": ""},
    {"maubot.yaml": "[not a mapping", "echo/__init__.py": ""},
])
def test_rejects(files):
    with pytest.raises(ValidationError) as e:
        validate_mbp(make_mbp(files), MAX)
    assert e.value.key == "err.mbp_invalid"


def test_rejects_non_zip_and_oversized():
    with pytest.raises(ValidationError):
        validate_mbp(b"not a zip", MAX)
    with pytest.raises(ValidationError) as e:
        validate_mbp(make_mbp({"maubot.yaml": META, "echo/__init__.py": "x" * 2000}), 1000)
    assert e.value.key == "err.file_too_large"


def test_missing_dependencies():
    assert missing_dependencies(["aiohttp>=3", "surely-not-installed-pkg==1"]) == ["surely-not-installed-pkg==1"]
