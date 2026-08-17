# Contributing

Please keep changes focused and include a reproducible test case when altering meshing, boundary conditions, result parsing, robot-load calculations or report output.

Before opening a pull request, run:

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```

Do not add customer STEP files, personal information, proprietary valve drawings, build directories, virtual environments or solver output directories to the repository.
