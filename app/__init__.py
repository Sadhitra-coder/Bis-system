"""
bis-rag-engine application package.

TENSORFLOW / KERAS GUARD
------------------------
transformers tries to import a TensorFlow backend when
TensorFlow is importable, and refuses to start if the installed
Keras is Keras 3:

    ValueError: Your currently installed version of Keras is
    Keras 3, but this is not yet supported in Transformers.

This project only ever uses the PyTorch backend (through
sentence-transformers), so the TF backend is dead weight. Both
flags below are read by transformers at import time, so they
must be set before anything imports transformers - which is
what this package __init__ guarantees for `import app.*`.

setdefault is used so an explicit environment value always
wins.
"""

import os

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
