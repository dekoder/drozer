#
# License: Refer to the README in the root directory
#

import math
import socket
import common
import logging
import functools
import xml.etree.cElementTree as etree

logging.basicConfig(name = 'Reflect', level = logging.DEBUG)
logsend = logging.getLogger(" -> ")
logrecv = logging.getLogger(" <- ")

class JavaReflectionException(Exception):
    pass

class Reflect(object):

    _functions = [("construct", 1, True),
                  ("invoke", 2, True),
                  ("setprop", 3, False),
                  ("getprop", 2, False),
                  ("delete", 1, False),
                  ("deleteall", 0, False),
                  ("resolve", 1, False),
                  ]

    # So pylint doesn't complain when it can't resolve dynamic attributes
    invoke = construct = resolve = getprop = setprop = delete = deleteall = object

    def __init__(self, session = None):
        if session == None:
            session = common.Session('127.0.0.1', 31415, None)
        self.session = session

        for funclist in self._functions:
            setattr(self, funclist[0], functools.partial(self._action, *funclist)) #pylint: disable-msg=W0142

    def _transceive(self, data):
        """Transcieves a reflection message"""
        try:
            # Open socket
            self.session.connectSocket()

            # Convert command to XML and send
            self.session.socketConn.sendall(data)

            # Receive until the socket is closed
            buf = self.session.socketConn.recv(1024)
            output = [buf]
            while buf:
                buf = self.session.socketConn.recv(1024)
                output.append(buf)
            data = "".join(output)

            # Close socket
            self.session.closeSocket()

            # Parse response from server
            return data

        except (socket.error, KeyboardInterrupt):
            # If we fail for a reason we know, return nothing
            try:
                self.session.closeSocket()
            except socket.error:
                pass

    def _action(self, name, numargs, additional, *args):
        """Produces the data and transceives it"""
        if (len(args) < numargs) or (not additional and len(args) != numargs):
            raise TypeError(name + "() takes exactly " + str(numargs) + " argument" + ("" if numargs == 1 else "s") + "(" + str(len(args)) + " given)")

        # Create document
        transmission = etree.Element("transmission")
        reflect = etree.Element("reflect")
        action = etree.Element("action", name = name)

        transmission.append(reflect)
        reflect.append(action)

        for arg in args[:numargs]:
            action.append(ReflectedTypeFactory(arg, self).to_element())

        if additional:
            addelem = etree.Element("arguments")
            action.append(addelem)
            for arg in args[numargs:]:
                addelem.append(ReflectedTypeFactory(arg, self).to_element())

        logsend.debug(etree.tostring(transmission, encoding = 'UTF-8'))

        # We must specify the encoding, or we won't get the <?xml ?> declaration
        response = self._transceive(etree.tostring(transmission, encoding = 'UTF-8'))
        logrecv.debug(response)

        if response:
            respelem = etree.fromstring(response).find('reflect/return-value')

            # Check we got back what we expected
            if not respelem:
                raise IOError("Transmission XML response does not contain return-value")

            # Process it based on whether it was a success or failure
            if respelem.get('type') == 'success' and len(respelem) == 1:
                return ElementToReflectedType(respelem[0], self)
            elif respelem.get('type') == 'success' and len(respelem) != 1:
                raise TypeError("Success response does not have exactly one response element")
            else:
                raise JavaReflectionException(respelem.get('errormsg', 'Unknown error occurred'))
        else:
            raise IOError(1, 'Empty response retrieved from action')

def ReflectedTypeFactory(obj, reflectobj):
    """Returns a (best guess) ReflectedType from a native type"""
    if isinstance(obj, ReflectedType):
        return obj
    elif isinstance(obj, long):
        return Reflectedprimitive("long", obj, reflect = reflectobj)
    elif isinstance(obj, int):
        return Reflectedprimitive("int", obj, reflect = reflectobj)
    elif isinstance(obj, float):
        return Reflectedprimitive("float", obj, reflect = reflectobj)
    elif isinstance(obj, bool):
        return Reflectedprimitive("bool", obj, reflect = reflectobj)
    elif isinstance(obj, str):
        return ReflectedString(obj, reflect = reflectobj)
    elif hasattr(obj, '__init__'):
        return ReflectedArray(obj, reflect = reflectobj)
    return None

def ElementToReflectedType(elem, reflectobj):
    """Returns a ReflectedType from an XML element"""
    if elem.tag == 'primitive':
        return Reflectedprimitive(elem.get('type'), elem.text, reflect = reflectobj)
    elif elem.tag == 'string':
        return ReflectedString(elem.text, reflect = reflectobj)
    elif elem.tag == 'array':
        array = []
        for i in elem:
            array.append(ElementToReflectedType(i, reflectobj))
        return ReflectedArray(array)
    elif elem.tag == 'objref':
        return ReflectedObjref(elem.text, reflect = reflectobj)
    return None

class ReflectedType(object):
    """Handles all types reflected from the server interface"""

    def __init__(self, reflect = None):
        if not isinstance(reflect, Reflect):
            raise TypeError("Reflection must be a Reflect object")
        self._reflect = reflect

    def _gettype(self, obj):
        """Determines the string representation of a ReflectedType"""
        if isinstance(obj, Reflectedprimitive):
            return obj.primitive_type
        elif isinstance(obj, ReflectedArray):
            return 'array'
        elif isinstance(obj, ReflectedString):
            return 'string'
        elif isinstance(obj, ReflectedObjref):
            return 'objref'
        return 'unknown'

    def to_element(self):
        """Returns an etree XML Element of the object"""
        raise NotImplementedError

class Reflectedprimitive(ReflectedType):
    """Class to handle Java primitive objects"""

    def __init__(self, primtype, native, *args, **kwargs):
        ReflectedType.__init__(self, *args, **kwargs)
        self._validate(primtype, native)
        self._type = primtype
        self._native = native

    def _validate(self, primtype, native):
        if primtype not in ['byte', 'short', 'int', 'long', 'float', 'double', 'bool', 'char']:
            raise TypeError("Specified type (" + primtype + ") is not a Java primitive")
        if primtype == 'byte':
            if not isinstance(native, str) and len(native) == 1:
                raise TypeError("Byte type requires single character string native equivalent")
        elif primtype == 'short':
            if not isinstance(native, int) and native < math.pow(2, 15) and native >= -(math.pow(2, 15)):
                raise TypeError("Short type not an integer or outside bounds")
        elif primtype == 'int':
            if not isinstance(native, int) and native < math.pow(2, 31) and native >= -(math.pow(2, 31)):
                raise TypeError("Integer type not an integer or outside bounds")
        elif primtype == 'long':
            if not isinstance(native, long) and native < math.pow(2, 63) and native >= -(math.pow(2, 63)):
                raise TypeError("Long type not a long or outside bounds")
        elif primtype == 'float' or primtype == 'double':
            if not isinstance(native, float):
                raise TypeError("Floating type not a float")
        elif primtype == 'bool':
            if not isinstance(native, bool):
                raise TypeError("Bool type not a boolean")
        elif primtype == 'char':
            if not isinstance(native, unicode) and len(native) == 1:
                raise TypeError("Char type requires single character unicode native equivalent")

    def to_element(self):
        elem = etree.Element('primitive', type = self._type)
        elem.text = unicode(self._native)
        return elem

    @property
    def primitive_type(self):
        return self._type

    @primitive_type.setter
    def primitive_type(self, value):
        self._validate(value, self._native)
        self._type = value

class ReflectedString(ReflectedType):
    """Class to handle Java strings"""

    def __init__(self, native, *args, **kwargs):
        ReflectedType.__init__(self, *args, **kwargs)
        self._native = native

    def to_element(self):
        elem = etree.Element('string')
        elem.text = unicode(self._native)
        return elem

class ReflectedArray(ReflectedType):
    """Class to handle Java arrays"""

    def __init__(self, objlist, *args, **kwargs):
        ReflectedType.__init__(self, *args, **kwargs)
        self._native = list(self._validate_and_convert(objlist))

    def _validate_and_convert(self, objlist):
        """Validates that all the objects in the array are the same type
           and converts them to ReflectedTypes
        """
        if not hasattr(objlist, '__iter__'):
            raise TypeError("Object list is not iterable")
        listtype = None
        for i in objlist:
            if not listtype:
                listtype = type(i)
            else:
                if type(i) != listtype:
                    raise TypeError("Not all elements in the array are the same type")
            yield ReflectedTypeFactory(i, self._reflect)

    def to_element(self):
        if len(self._native) < 1:
            return etree.Element('array', type = 'unknown')
        else:
            elem = etree.Element('array', type = self._gettype(self._native[0]))
            for i in self._native:
                subelem = ReflectedTypeFactory(i, self._reflect).to_element()
                elem.append(subelem)
            return elem

    def __len__(self, *args, **kwargs):
        return self._native.__len__(*args, **kwargs)

    def __getitem__(self, *args, **kwargs):
        return self._native.__getitem__(*args, **kwargs)

class ReflectedObjref(ReflectedType):
    """Class to handle Java Objects"""

    def __init__(self, objref, *args, **kwargs):
        ReflectedType.__init__(self, *args, **kwargs)
        self._objref = objref
        self._methodnames = set()
        self._fieldnames = set()

    def to_element(self):
        elem = etree.Element('objref')
        elem.text = self._objref
        return elem

    def __getattr__(self, attr):
        """Overrides the getattr function to support fields and methods"""
        # Check that the field/method lists have been retrieved
        self._get_fields()
        self._get_methods()
        if attr in self._fieldnames:
            return property(functools.partial(self._fieldgetter, attr), functools.partial(self._fieldsetter, attr))
        if attr in self._methodnames:
            return functools.partial(self._invoker, attr)

    def _fieldgetter(self, name):
        self._reflect.getprop(self, name)

    def _fieldsetter(self, name, value):
        self._reflect.setprop(self, name, value)

    def _invoker(self, attr, *args, **kwargs):
        return self._reflect.invoke(self, attr, *args, **kwargs)

    def _get_fields(self):
        if not self._fieldnames:
            cls = self._reflect.invoke(self, 'getClass')
            fields = self._reflect.invoke(cls, 'getFields')
            for field in fields:
                self._fieldnames.add(str(self._reflect.invoke(field, 'getName')))

    def _get_methods(self):
        if not self._methodnames:
            cls = self._reflect.invoke(self, 'getClass')
            methods = self._reflect.invoke(cls, 'getMethods')
            for method in methods:
                self._methodnames.add(str(self._reflect.invoke(method, 'getName')))

if __name__ == '__main__':
    refl = Reflect()
    oref = refl.resolve('java.lang.Reflect')
