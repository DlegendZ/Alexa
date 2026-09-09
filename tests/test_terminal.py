"""The terminal client's one piece of real machinery: who gets the line.

Everything else in `main.py` is printing. This is not: there is one file
descriptor, and two things want what arrives on it -- the turn loop, which
takes questions, and a confirmation, which has to be answered in the middle of
a turn. Reading it from both is two threads racing, and the loser waits for a
line that has already been handed to the winner.
"""

from __future__ import annotations

import queue
import threading
import time

import pytest

from sunday.main import Keyboard
from sunday.runtime import ConfirmRequest

REQUEST = ConfirmRequest(question="Overwrite notes.txt (67 bytes)?", path="notes.txt")


class Typed:
    """stdin, one line at a time, on demand -- so a test can decide *when* a
    line arrives as well as what it says."""

    def __init__(self) -> None:
        self.lines: queue.Queue[str] = queue.Queue()

    def __call__(self, *_args) -> str:
        line = self.lines.get(timeout=3)
        if line is EOFError:  # pragma: no cover - sentinel comparison
            raise EOFError
        return line

    def send(self, line: str) -> None:
        self.lines.put(line)

    def close(self) -> None:
        self.lines.put(EOFError)  # type: ignore[arg-type]


@pytest.fixture
def keyboard(monkeypatch, capsys):
    typed = Typed()
    monkeypatch.setattr("builtins.input", typed)
    board = Keyboard(queue.Queue())
    board.start()
    yield board, typed
    typed.close()


def settle(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_a_typed_line_is_a_question(keyboard):
    board, typed = keyboard
    typed.send("what is the gold price")
    assert board.inbox.get(timeout=3) == ("text", "what is the gold price")


def test_the_answer_to_a_confirmation_does_not_become_the_next_question(keyboard):
    """The bug. Two readers on one descriptor, and the turn loop was as likely
    to take the `y` -- so the file was left alone, the confirmation waited out
    its timeout, and "y" arrived afterwards as a question."""
    board, typed = keyboard
    answered: list[bool] = []
    threading.Thread(
        target=lambda: answered.append(board.confirm(REQUEST)), daemon=True
    ).start()

    assert settle(lambda: board._awaiting.is_set())
    typed.send("y")

    assert settle(lambda: answered == [True])
    assert board.inbox.empty(), "the answer was also treated as a question"


@pytest.mark.parametrize("answer,expected", [("y", True), ("yes", True), ("Y", True),
                                             ("n", False), ("", False), ("later", False)])
def test_anything_that_is_not_a_yes_is_a_no(keyboard, answer, expected):
    board, typed = keyboard
    answered: list[bool] = []
    threading.Thread(
        target=lambda: answered.append(board.confirm(REQUEST)), daemon=True
    ).start()
    assert settle(lambda: board._awaiting.is_set())
    typed.send(answer)
    assert settle(lambda: answered == [expected]), answered


def test_questions_flow_again_once_the_confirmation_is_answered(keyboard):
    board, typed = keyboard
    answered: list[bool] = []
    threading.Thread(
        target=lambda: answered.append(board.confirm(REQUEST)), daemon=True
    ).start()
    assert settle(lambda: board._awaiting.is_set())
    typed.send("n")
    assert settle(lambda: answered == [False])

    typed.send("and the weather")
    assert board.inbox.get(timeout=3) == ("text", "and the weather")


def test_a_closed_stdin_releases_a_waiting_confirmation(keyboard):
    """Or the turn hangs for two minutes on a descriptor that will never speak
    again, and the answer it eventually gets is the timeout's anyway."""
    board, typed = keyboard
    answered: list[bool] = []
    threading.Thread(
        target=lambda: answered.append(board.confirm(REQUEST)), daemon=True
    ).start()
    assert settle(lambda: board._awaiting.is_set())

    typed.close()
    assert settle(lambda: answered == [False])


def test_exit_ends_the_loop(keyboard):
    board, typed = keyboard
    typed.send("exit")
    assert board.inbox.get(timeout=3) is None
