"""Global test-process hygiene for release-payload tests.

The candidate builder intentionally rejects bytecode directories and files in
the installable tree.  Pytest imports source helpers in-process, so disable
interpreter bytecode writes before collection; this keeps test order from
creating residue that changes later candidate-manifest results.  Subprocess
fixtures set the equivalent environment explicitly as well.
"""
import sys


sys.dont_write_bytecode = True
