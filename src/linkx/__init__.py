"""linkx: a concurrent producer/consumer web link extractor."""

import logging

__version__ = "0.1.0"

# A library leaves log output to the application: without this, Python's last-resort
# handler would print every warning when linkx is used as a library.
logging.getLogger(__name__).addHandler(logging.NullHandler())
