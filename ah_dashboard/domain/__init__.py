"""What the app knows, independent of AH's API shapes and of Flask.

- :mod:`.models` — offers, stores, the Bonus week; ``to_json()`` is the API
  contract the page's JavaScript reads.
- :mod:`.pricing` — bonus prices and deal types from discount labels.
- :mod:`.dates` — the Dutch calendar date (AH's bonus weeks).

Nothing here does I/O; ``ah.mapping`` turns AH's answers into these models.
"""
