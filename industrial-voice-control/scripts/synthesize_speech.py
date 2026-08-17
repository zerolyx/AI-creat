#!/usr/bin/env python3
import base64
import sys

import pyttsx3


def main():
    if len(sys.argv) != 5:
        return 2
    output_path, voice_id, rate_text, encoded_text = sys.argv[1:]
    text = base64.b64decode(encoded_text).decode("utf-8").strip()
    if not text:
        return 3

    engine = pyttsx3.init()
    try:
        engine.setProperty("voice", voice_id)
        engine.setProperty("rate", int(rate_text))
        engine.setProperty("volume", 1.0)
        engine.save_to_file(text, output_path)
        engine.runAndWait()
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
