
import asyncio
import logging
import itertools
from six import string_types

from transitions.core import (Condition, Event, EventData, Machine,
                              MachineError, State, Transition)
from .nesting import (HierarchicalMachine, NestedState,
                      NestedTransition, NestedEvent)


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class AsyncNestedState(NestedState):

    async def enter(self, event_data):
        """ Triggered when a state is entered. """
        logger.debug("%sEntering state %s. Processing callbacks...", event_data.machine.name, self.name)
        for oe in self.on_enter:
            await event_data.machine._callback(oe, event_data)
        logger.info("%sEntered state %s", event_data.machine.name, self.name)

    async def exit(self, event_data):
        """ Triggered when a state is exited. """
        logger.debug("%sExiting state %s. Processing callbacks...", event_data.machine.name, self.name)
        for oe in self.on_exit:
            await event_data.machine._callback(oe, event_data)
        logger.info("%sExited state %s", event_data.machine.name, self.name)

    async def exit_nested(self, event_data, target_state):
        if self.level > target_state.level:
            await self.exit(event_data)
            return (await self.parent.exit_nested(event_data, target_state))
        elif self.level <= target_state.level:
            tmp_state = target_state
            while self.level != tmp_state.level:
                tmp_state = tmp_state.parent
            tmp_self = self
            while tmp_self.level > 0 and tmp_state.parent.name != tmp_self.parent.name:
                await tmp_self.exit(event_data)
                tmp_self = tmp_self.parent
                tmp_state = tmp_state.parent
            if tmp_self != tmp_state:
                await tmp_self.exit(event_data)
                return tmp_self.level
            else:
                return tmp_self.level + 1

    async def enter_nested(self, event_data, level=None):
        if level is not None and level <= self.level:
            if level != self.level:
                await self.parent.enter_nested(event_data, level)
            await self.enter(event_data)


class AsyncNestedCondition(Condition):

    async def check(self, event_data):
        return (await super().check(event_data))

    async def _condition_check(self, statement):
        if asyncio.iscoroutine(statement):
            statement = await statement

        return statement == self.target


class AsyncNestedTransition(NestedTransition):

    condition_cls = AsyncNestedCondition

    async def execute(self, event_data):

        logger.debug("%sInitiating transition from state %s to state %s...",
                     event_data.machine.name, self.source, self.dest)
        machine = event_data.machine

        for func in self.prepare:
            await machine._callback(func, event_data)
            logger.debug("Executed callback '%s' before conditions." % func)

        for c in self.conditions:
            if not (await c.check(event_data)):
                logger.warning("%sTransition condition failed: %s() does not " +
                             "return %s. Transition halted.", event_data.machine.name, c.func, c.target)
                return False
        for func in itertools.chain(machine.before_state_change, self.before):
            await machine._callback(func, event_data)
            logger.debug("%sExecuted callback '%s' before transition.", event_data.machine.name, func)

        await self._change_state(event_data)

        for func in itertools.chain(self.after, machine.after_state_change):
            await machine._callback(func, event_data)
            logger.debug("%sExecuted callback '%s' after transition.", event_data.machine.name, func)
        return True

    async def _change_state(self, event_data):

        machine = event_data.machine
        model = event_data.model
        dest_state = machine.get_state(self.dest)
        source_state = machine.get_state(model.state)
        lvl = await source_state.exit_nested(event_data, dest_state)
        event_data.machine.set_state(self.dest, model)
        event_data.update(model)
        await dest_state.enter_nested(event_data, lvl)


class AsyncNestedEvent(NestedEvent):

    lock = asyncio.Lock()  # Global lock to be acquired to start transitions

    async def trigger(self, *args, **kwargs):
        """To start a transition we will have to await this function
        and the next to come. The goal is to have just one task
        that switches ON on entering a state and off when exiting.
        We must be sure though, that no other transitions are going to occour
        otherwise we risk a data race condition.
        """
        with (await self.lock):
            return (await super().trigger(*args, **kwargs))

    async def _trigger(self, model, *args, **kwargs):
        state = self.machine.get_state(model.state)
        while state.parent and state.name not in self.transitions:
            state = state.parent
        if state.name not in self.transitions:
            msg = "%sCan't trigger event %s from state %s!" % (self.machine.name, self.name,
                                                               model.state)
            if self.machine.get_state(model.state).ignore_invalid_triggers:
                logger.warning(msg)
            else:
                raise MachineError(msg)
        event_data = EventData(self.machine.get_state(model.state), self, self.machine,
                               model, args=args, kwargs=kwargs)

        for func in self.machine.prepare_event:
            await self.machine._callback(func, event_data)
            logger.debug("Executed machine preparation callback '%s' before conditions." % func)

        try:
            for t in self.transitions[state.name]:
                event_data.transition = t
                transition_result = await t.execute(event_data)
                if transition_result:
                    event_data.result = True
                    break
        except Exception as e:
            event_data.error = e
            raise
        finally:
            for func in self.machine.finalize_event:
                await self.machine._callback(func, event_data)
                logger.debug("Executed machine finalize callback '%s'." % func)
        return event_data.result


class AsyncHierarchicalMachine(HierarchicalMachine):

    state_cls = AsyncNestedState
    transition_cls = AsyncNestedTransition
    event_cls = AsyncNestedEvent

    async def _callback(self, func, event_data):
        if isinstance(func, string_types):
            func = getattr(event_data.model, func)

        if self.send_event:
            callback = func(event_data)
        else:
            callback = func(*event_data.args, **event_data.kwargs)

        if asyncio.iscoroutine(callback):
            await callback

    async def _process(self, trigger):
        if not self.has_queue:
            if not self._transition_queue:
                return (await trigger())
            else:
                raise MachineError(
                    "Attempt to process events synchronously while transition queue is not empty!"
                )

        self._transition_queue.append(trigger)
        if len(self._transition_queue) > 1:
            return True

        while self._transition_queue:
            try:
                callback = self._transition_queue[0]()

                if asyncio.iscoroutine(callback):
                    await callback

                self._transition_queue.popleft()
            except Exception:
                self._transition_queue.clear()
                raise
        return True
