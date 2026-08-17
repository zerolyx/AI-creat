from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

if "--worker" in sys.argv:
    if sys.stdout is None:
        sys.stdout = open(Path.cwd() / "packaged_worker.log", "a", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    from ai_fea_mvp.cli import main as worker_main
else:
    from ai_fea_mvp.gui import main as gui_main


if __name__ == "__main__":
    if "--worker" in sys.argv:
        arguments = [argument for argument in sys.argv[1:] if argument != "--worker"]
        try:
            raise SystemExit(worker_main(arguments))
        except SystemExit:
            raise
        except Exception:
            error_text = traceback.format_exc()
            try:
                workdir_index = arguments.index("--workdir")
                error_path = Path(arguments[workdir_index + 1]) / "worker_error.log"
                error_path.parent.mkdir(parents=True, exist_ok=True)
                error_path.write_text(error_text, encoding="utf-8")
            except Exception:
                pass
            if sys.stdout is not None:
                sys.stdout.write(error_text)
            raise SystemExit(1)
    raise SystemExit(gui_main())
