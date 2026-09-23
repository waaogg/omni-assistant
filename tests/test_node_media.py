import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_http_media_builder_sends_image_bytes_and_text(tmp_path: Path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nexample")
    document = tmp_path / "note.txt"
    document.write_text("attachment body", encoding="utf-8")
    script = """
      const provider = require('./core/ai_provider.js');
      const result = provider.buildMultimodalContent(process.argv[1], [process.argv[2], process.argv[3]]);
      console.log(JSON.stringify(result));
    """
    run = subprocess.run(
        ["node", "-e", script, "question", str(image), str(document)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(run.stdout)
    assert result[0]["type"] == "text"
    assert "attachment body" in result[0]["text"]
    assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")
