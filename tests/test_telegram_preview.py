"""The Telegram preview is only a copy for Max: if Telegram drops the connection, the run
must carry on and schedule the rest of its edits (run 46161181220 on 28 Nov 2026 died on a
'Connection reset by peer' after scheduling the first edit, losing the others)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import ci_run  # noqa: E402

ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}


class TelegramPreview(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name)
        (self.folder / "video.mp4").write_bytes(b"x")
        (self.folder / "preview.mp4").write_bytes(b"x")

    def tearDown(self):
        self.dir.cleanup()

    def test_connection_reset_does_not_stop_the_run(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(ci_run.subprocess, "run"), \
             mock.patch.object(ci_run.requests, "post",
                               side_effect=requests.exceptions.ConnectionError("Connection reset by peer")):
            ci_run.telegram_preview("e1", self.folder, "note")   # must not raise

    def test_ffmpeg_failure_does_not_stop_the_run(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(
                ci_run.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            ci_run.telegram_preview("e1", self.folder, "note")   # must not raise

    def test_still_sends_when_telegram_is_up(self):
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"ok": True}
        with mock.patch.dict(os.environ, ENV), mock.patch.object(ci_run.subprocess, "run"), \
             mock.patch.object(ci_run.requests, "post", return_value=ok) as post:
            ci_run.telegram_preview("e1", self.folder, "note")
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
