# -*- coding: utf-8 -*-

try:
    from builtins import object
except ImportError:
    pass

import asyncio


from transitions.extensions import MachineFactory
from transitions.extensions.asynchronous import AsyncNestedState as State
from transitions.extensions.asynchronous import AsyncNestedEvent
from .test_core import TestTransitions as TestsCore
from .utils import Stuff


from unittest import TestCase

try:
    from unittest.mock import MagicMock
except ImportError:
    from mock import MagicMock

state_separator = State.separator


class TestProceduralTransitions(TestsCore):
    """Test that in an async context the awaited triggers respect the original
    machine behavior
    """
    def setUp(self):
        states = ['A', 'B', {'name': 'C', 'children': ['1', '2', {'name': '3', 'children': ['a', 'b', 'c']}]},
                  'D', 'E', 'F']

        machine_cls = MachineFactory.get_predefined(nested=True, async=True)

        class AsyncCompatibilityEventClass(AsyncNestedEvent):

            def trigger(self, model, *args, **kwargs):
                loop = asyncio.get_event_loop()
                return loop.run_until_complete(super().trigger(model, *args, **kwargs))


        machine_cls.event_cls = AsyncCompatibilityEventClass
        self.stuff = Stuff(states, machine_cls)

    def tearDown(self):
        State.separator = state_separator


# class AsyncMock():
#
#     """Class with some mock methods that can be used as state machine
#     callbacks
#     """
#
#     def __getattr__(self, name):
#         mock = MagicMock()
#         setattr(self, '%s_mock' % name, mock)
#
#         async def wrapper(*args, **kwargs):
#             return mock(*args, **kwargs)
#
#         return wrapper
#
#
# def run_async_test(coro, timeout=None):
#     loop = asyncio.get_event_loop()
#     loop.run_until_complete(coro)
#     loop.close()


class TestAsyncStuff(TestCase):
    """Test that triggers async callbacks respect the machine logic"""

    pass
