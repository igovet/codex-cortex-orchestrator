"""Dependency-neutral primitives for Cortex runtime slices.

The executable ``cortex.py`` module is the composition root.  Modules in this
package must not import it; runtime-specific collaborators are supplied there
through the explicit binding port.
"""

