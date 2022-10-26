from __future__ import annotations

from enum import auto, Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional

class BatchType(str, Enum):
    CHATHISTORY = "chathistory"
    NETJOIN = "netjoin"
    NETSPLIT = "netsplit"


class Batch:
    reference: str
    """This batch's opaque reference tag"""
    batchtype: BatchType
    """The type of the batch"""
    parent: Optional[Batch] = None
    """The parent Batch, if nested"""
    messages: list

    def __init__(self, pretrigger: PreTrigger, parent: Optional[Batch] = None):
        self.reference = pretrigger[1][1:]
        self.batchtype = BatchType(pretrigger[2])
        self.parent = parent
        self.messages = []
