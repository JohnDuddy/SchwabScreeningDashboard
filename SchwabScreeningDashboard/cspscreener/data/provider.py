"""
Pluggable data-provider interface.

A provider exposes:
  fetch_stock(ticker)  -> StockSnapshot   (price, volume, fundamentals, technicals, events)
  fetch_options(ticker, spot)  -> list[OptionCandidate]   (raw put rows, pre-filter)

To swap in Polygon / Tradier / Schwab, write a class implementing the same
interface and pass it to the screening pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import StockSnapshot, OptionCandidate


class DataProvider(ABC):
    """Abstract base — implement to add a new data source."""

    @abstractmethod
    def fetch_stock(self, ticker: str) -> Optional[StockSnapshot]:
        ...

    @abstractmethod
    def fetch_options(self, ticker: str, spot: float) -> List[OptionCandidate]:
        ...
