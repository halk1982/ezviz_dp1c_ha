"""Check runtime prerequisites before accessing the account or starting a server."""
import importlib
import subprocess


def main():
    try:
        # Import the real pipeline: this also checks the pinned library's private
        # streaming helpers, including those used only by encrypted feeds.
        probe = importlib.import_module("probe")
        importlib.import_module("live")
        for executable in ("ffmpeg", "ffprobe"):
            subprocess.run([executable, "-version"], capture_output=True, check=True, timeout=5)
    except Exception as error:
        print(f"FAIL: stage=startup_check error_class={type(error).__name__}", flush=True)
        print("HINT: rebuild the add-on with its pinned Python dependencies, FFmpeg and FFprobe.", flush=True)
        return 1
    print("STARTUP_CHECK: imports, FFmpeg and FFprobe available", flush=True)
    return probe.main()


if __name__ == "__main__":
    raise SystemExit(main())
