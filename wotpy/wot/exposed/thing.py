#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Classes that represent Things exposed by a servient.
"""

import tornado.gen
from rx import Observable
from rx.subjects import Subject
from tornado.concurrent import Future

from wotpy.td.description import ThingDescription
from wotpy.td.interaction import Property, Action, Event
from wotpy.utils.enums import EnumListMixin
from wotpy.wot.dictionaries import \
    PropertyChangeEventInit, \
    ThingDescriptionChangeEventInit, \
    ActionInvocationEventInit
from wotpy.wot.enums import DefaultThingEvent, TDChangeMethod, TDChangeType
from wotpy.wot.events import \
    EmittedEvent, \
    PropertyChangeEmittedEvent, \
    ThingDescriptionChangeEmittedEvent, \
    ActionInvocationEmittedEvent
from wotpy.wot.interfaces.consumed import AbstractConsumedThing
from wotpy.wot.interfaces.exposed import AbstractExposedThing


class ExposedThing(AbstractConsumedThing, AbstractExposedThing):
    """An entity that serves to define the behavior of a Thing.
    An application uses this class when it acts as the Thing 'server'."""

    class HandlerKeys(EnumListMixin):
        """Enumeration of handler keys."""

        RETRIEVE_PROPERTY = "retrieve_property"
        UPDATE_PROPERTY = "update_property"
        INVOKE_ACTION = "invoke_action"
        OBSERVE = "observe"

    class InteractionStateKeys(EnumListMixin):
        """Enumeration of interaction state keys."""

        PROPERTY_VALUES = "property_values"

    def __init__(self, servient, thing):
        self._servient = servient
        self._thing = thing

        self._interaction_states = {
            self.InteractionStateKeys.PROPERTY_VALUES: {}
        }

        self._handlers_global = {
            self.HandlerKeys.RETRIEVE_PROPERTY: self._default_retrieve_property_handler,
            self.HandlerKeys.UPDATE_PROPERTY: self._default_update_property_handler,
            self.HandlerKeys.INVOKE_ACTION: self._default_invoke_action_handler
        }

        self._handlers = {
            self.HandlerKeys.RETRIEVE_PROPERTY: {},
            self.HandlerKeys.UPDATE_PROPERTY: {},
            self.HandlerKeys.INVOKE_ACTION: {}
        }

        self._events_stream = Subject()

    def __eq__(self, other):
        return self.servient == other.servient and self.thing == other.thing

    def __hash__(self):
        return hash((self.servient, self.thing))

    def _set_property_value(self, prop, value):
        """Sets a Property value."""

        prop_values = self.InteractionStateKeys.PROPERTY_VALUES
        self._interaction_states[prop_values][prop] = value

    def _get_property_value(self, prop):
        """Returns a Property value."""

        prop_values = self.InteractionStateKeys.PROPERTY_VALUES
        return self._interaction_states[prop_values].get(prop, None)

    def _set_handler(self, handler_type, handler, interaction=None):
        """Sets the currently defined handler for the given handler type."""

        if interaction is None or handler_type not in self._handlers:
            self._handlers_global[handler_type] = handler
        else:
            self._handlers[handler_type][interaction] = handler

    def _get_handler(self, handler_type, interaction=None):
        """Returns the currently defined handler for the given handler type."""

        interaction_handler = self._handlers.get(handler_type, {}).get(interaction, None)
        return interaction_handler or self._handlers_global[handler_type]

    def _find_interaction(self, name):
        """Raises ValueError if the given interaction does not exist in this Thing."""

        interaction = self._thing.find_interaction(name=name)

        if not interaction:
            raise ValueError("Interaction not found: {}".format(name))

        return interaction

    def _default_retrieve_property_handler(self, property_name):
        """Default handler for property reads."""

        future_read = Future()
        prop = self._find_interaction(name=property_name)
        prop_value = self._get_property_value(prop)
        future_read.set_result(prop_value)

        return future_read

    def _default_update_property_handler(self, property_name, value):
        """Default handler for onUpdateProperty."""

        future_write = Future()
        prop = self._find_interaction(name=property_name)
        self._set_property_value(prop, value)
        future_write.set_result(None)

        return future_write

    # noinspection PyMethodMayBeStatic
    def _default_invoke_action_handler(self):
        """Default handler for onInvokeAction."""

        future_invoke = Future()
        future_invoke.set_exception(Exception("Undefined action handler"))

        return future_invoke

    @property
    def servient(self):
        """Servient that contains this ExposedThing."""

        return self._servient

    @property
    def url_name(self):
        """Slug version (URL-safe) of the ExposedThing name."""

        return self._thing.url_name

    @property
    def name(self):
        """Name property."""

        return self._thing.name

    @property
    def thing(self):
        """Returns the object that represents the Thing beneath this ExposedThing."""

        return self._thing

    def get_thing_description(self):
        """Returns the Thing Description of the Thing.
        Returns a serialized string."""

        return self._thing.to_jsonld_str()

    @tornado.gen.coroutine
    def read_property(self, name):
        """Takes the Property name as the name argument, then requests from
        the underlying platform and the Protocol Bindings to retrieve the
        Property on the remote Thing and return the result. Returns a Future
        that resolves with the Property value or rejects with an Error."""

        interaction = self._find_interaction(name=name)

        handler = self._get_handler(
            handler_type=self.HandlerKeys.RETRIEVE_PROPERTY,
            interaction=interaction)

        value = yield handler(name)

        raise tornado.gen.Return(value)

    @tornado.gen.coroutine
    def write_property(self, name, value):
        """Takes the Property name as the name argument and the new value as the
        value argument, then requests from the underlying platform and the Protocol
        Bindings to update the Property on the remote Thing and return the result.
        Returns a Future that resolves on success or rejects with an Error."""

        interaction = self._find_interaction(name=name)

        if not interaction.writable:
            raise TypeError("Property is non-writable")

        handler = self._get_handler(
            handler_type=self.HandlerKeys.UPDATE_PROPERTY,
            interaction=interaction)

        yield handler(name, value)

        event_init = PropertyChangeEventInit(name=name, value=value)
        self._events_stream.on_next(PropertyChangeEmittedEvent(init=event_init))

    @tornado.gen.coroutine
    def invoke_action(self, name, *args, **kwargs):
        """Takes the Action name from the name argument and the list of parameters,
        then requests from the underlying platform and the Protocol Bindings to
        invoke the Action on the remote Thing and return the result. Returns a
        Promise that resolves with the return value or rejects with an Error."""

        interaction = self._find_interaction(name=name)

        handler = self._get_handler(
            handler_type=self.HandlerKeys.INVOKE_ACTION,
            interaction=interaction)

        result = yield handler(*args, **kwargs)

        event_init = ActionInvocationEventInit(action_name=name, return_value=result)
        emitted_event = ActionInvocationEmittedEvent(init=event_init)
        self._events_stream.on_next(emitted_event)

        raise tornado.gen.Return(result)

    def on_event(self, name):
        """Returns an Observable for the Event specified in the name argument,
        allowing subscribing to and unsubscribing from notifications."""

        try:
            self._find_interaction(name=name)
        except ValueError:
            # noinspection PyUnresolvedReferences
            return Observable.throw(Exception("Unknown event"))

        def event_filter(item):
            return item.name == name

        # noinspection PyUnresolvedReferences
        return self._events_stream.filter(event_filter)

    def on_property_change(self, name):
        """Returns an Observable for the Property specified in the name argument,
        allowing subscribing to and unsubscribing from notifications."""

        try:
            interaction = self._find_interaction(name=name)
        except ValueError:
            # noinspection PyUnresolvedReferences
            return Observable.throw(Exception("Unknown property"))

        if not interaction.observable:
            # noinspection PyUnresolvedReferences
            return Observable.throw(Exception("Property is not observable"))

        def property_change_filter(item):
            return item.name == DefaultThingEvent.PROPERTY_CHANGE and \
                   item.data.name == name

        # noinspection PyUnresolvedReferences
        return self._events_stream.filter(property_change_filter)

    def on_td_change(self):
        """Returns an Observable, allowing subscribing to and unsubscribing
        from notifications to the Thing Description."""

        def td_change_filter(item):
            return item.name == DefaultThingEvent.DESCRIPTION_CHANGE

        # noinspection PyUnresolvedReferences
        return self._events_stream.filter(td_change_filter)

    def expose(self):
        """Start serving external requests for the Thing, so that
        WoT interactions using Properties, Actions and Events will be possible."""

        self._servient.enable_exposed_thing(self.thing.id)

    def destroy(self):
        """Stop serving external requests for the Thing and destroy the object.
        Note that eventual unregistering should be done before invoking this method."""

        self._servient.remove_exposed_thing(self.thing.id)

    def emit_event(self, event_name, payload):
        """Emits an the event initialized with the event name specified by
        the event_name argument and data specified by the payload argument."""

        if not self.thing.find_interaction(name=event_name):
            raise ValueError("Unknown event: {}".format(event_name))

        self._events_stream.on_next(EmittedEvent(name=event_name, init=payload))

    def add_property(self, name, property_init):
        """Adds a Property defined by the argument and updates the Thing Description.
        Takes an instance of ThingPropertyInit as argument."""

        prop = Property(
            thing=self._thing,
            id=name,
            label=property_init.label,
            description=property_init.description,
            value_type=property_init.value_type,
            writable=property_init.writable,
            observable=property_init.observable)

        self._thing.add_interaction(prop)
        self._set_property_value(prop, property_init.value)

        event_data = ThingDescriptionChangeEventInit(
            td_change_type=TDChangeType.PROPERTY,
            method=TDChangeMethod.ADD,
            name=name,
            data=property_init.to_dict(),
            description=ThingDescription.from_thing(self.thing).to_dict())

        self._events_stream.on_next(ThingDescriptionChangeEmittedEvent(init=event_data))

    def remove_property(self, name):
        """Removes the Property specified by the name argument,
        updates the Thing Description and returns the object."""

        self._thing.remove_interaction(name=name)

        event_data = ThingDescriptionChangeEventInit(
            td_change_type=TDChangeType.PROPERTY,
            method=TDChangeMethod.REMOVE,
            name=name)

        self._events_stream.on_next(ThingDescriptionChangeEmittedEvent(init=event_data))

    def add_action(self, name, action_init):
        """Adds an Action to the Thing object as defined by the action
        argument of type ThingActionInit and updates th,e Thing Description."""

        action = Action(
            thing=self._thing,
            id=name,
            label=action_init.label,
            description=action_init.description,
            output=action_init.output,
            input=action_init.input)

        self._thing.add_interaction(action)

        event_data = ThingDescriptionChangeEventInit(
            td_change_type=TDChangeType.ACTION,
            method=TDChangeMethod.ADD,
            name=name,
            data=action_init.to_dict(),
            description=ThingDescription.from_thing(self.thing).to_dict())

        self._events_stream.on_next(ThingDescriptionChangeEmittedEvent(init=event_data))

    def remove_action(self, name):
        """Removes the Action specified by the name argument,
        updates the Thing Description and returns the object."""

        self._thing.remove_interaction(name=name)

        event_data = ThingDescriptionChangeEventInit(
            td_change_type=TDChangeType.ACTION,
            method=TDChangeMethod.REMOVE,
            name=name)

        self._events_stream.on_next(ThingDescriptionChangeEmittedEvent(init=event_data))

    def add_event(self, name, event_init):
        """Adds an event to the Thing object as defined by the event argument
        of type ThingEventInit and updates the Thing Description."""

        event = Event(
            thing=self._thing,
            id=name,
            label=event_init.label,
            description=event_init.description,
            value_type=event_init.value_type)

        self._thing.add_interaction(event)

        event_data = ThingDescriptionChangeEventInit(
            td_change_type=TDChangeType.EVENT,
            method=TDChangeMethod.ADD,
            name=name,
            data=event_init.to_dict(),
            description=ThingDescription.from_thing(self.thing).to_dict())

        self._events_stream.on_next(ThingDescriptionChangeEmittedEvent(init=event_data))

    def remove_event(self, name):
        """Removes the event specified by the name argument,
        updates the Thing Description and returns the object."""

        self._thing.remove_interaction(name=name)

        event_data = ThingDescriptionChangeEventInit(
            td_change_type=TDChangeType.EVENT,
            method=TDChangeMethod.REMOVE,
            name=name)

        self._events_stream.on_next(ThingDescriptionChangeEmittedEvent(init=event_data))

    def set_action_handler(self, action_handler, action_name=None):
        """Takes an action_name as an optional string argument, and an action handler.
        Sets the handler function for the specified Action matched by action_name if
        action_name is specified, otherwise sets it for any action. Throws on error."""

        interaction = None

        if action_name is not None:
            interaction = self._find_interaction(name=action_name)

        self._set_handler(
            handler_type=self.HandlerKeys.INVOKE_ACTION,
            handler=action_handler,
            interaction=interaction)

    def set_property_read_handler(self, read_handler, property_name=None):
        """Takes a property_name as an optional string argument, and a property read handler.
        Sets the handler function for reading the specified Property matched by property_name if
        property_name is specified, otherwise sets it for reading any property. Throws on error."""

        interaction = None

        if property_name is not None:
            interaction = self._find_interaction(name=property_name)

        self._set_handler(
            handler_type=self.HandlerKeys.RETRIEVE_PROPERTY,
            handler=read_handler,
            interaction=interaction)

    def set_property_write_handler(self, write_handler, property_name=None):
        """Takes a property_name as an optional string argument, and a property write handler.
        Sets the handler function for writing the specified Property matched by property_name if the
        property_name is specified, otherwise sets it for writing any properties. Throws on error."""

        interaction = None

        if property_name is not None:
            interaction = self._find_interaction(name=property_name)

        self._set_handler(
            handler_type=self.HandlerKeys.UPDATE_PROPERTY,
            handler=write_handler,
            interaction=interaction)

    @property
    def properties(self):
        """Represents a dictionary of ThingProperty items."""

        raise NotImplementedError()

    @property
    def actions(self):
        """Represents a dictionary of ThingAction items."""

        raise NotImplementedError()

    @property
    def events(self):
        """Represents a dictionary of ThingEvent items."""

        raise NotImplementedError()

    @property
    def links(self):
        """Represents a dictionary of WebLink items."""

        raise NotImplementedError()

    def subscribe(self):
        """Subscribes to changes on the TD of this thing."""

        raise NotImplementedError()
