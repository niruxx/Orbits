"""Request an optional desktop screenshot through the standard desktop portal."""
import uuid


def capture(callback):
    from gi.repository import Gio, GLib
    token="orbits_"+uuid.uuid4().hex
    state={"subscription":None,"timeout":None,"finished":False,"path":None}
    connection=None
    def finish(path,error):
        if state["finished"]:return
        state["finished"]=True
        if state["subscription"] is not None:connection.signal_unsubscribe(state["subscription"])
        if state["timeout"] is not None:GLib.source_remove(state["timeout"])
        callback(path,error)
    try:
        connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        sender=connection.get_unique_name().lstrip(":").replace(".","_")
        state["path"]=f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        def response(_connection,_sender,path,_interface,_signal,parameters,_user_data):
            if path!=state["path"]:return
            code,results=parameters.unpack()
            if code:
                finish(None,RuntimeError("Screenshot was cancelled or denied"));return
            uri=results.get("uri","")
            path=Gio.File.new_for_uri(uri).get_path() if uri else None
            finish(path,None if path else RuntimeError("The portal did not return a local screenshot"))
        state["subscription"]=connection.signal_subscribe("org.freedesktop.portal.Desktop","org.freedesktop.portal.Request","Response",
                                                          None,None,Gio.DBusSignalFlags.NONE,response,None)
        def requested(bus,result):
            try:
                handle=bus.call_finish(result).unpack()[0]
                state["path"]=handle
            except GLib.Error as error:finish(None,error)
        options={"handle_token":GLib.Variant("s",token),"interactive":GLib.Variant("b",False)}
        connection.call("org.freedesktop.portal.Desktop","/org/freedesktop/portal/desktop","org.freedesktop.portal.Screenshot","Screenshot",
                        GLib.Variant("(sa{sv})",("",options)),GLib.VariantType("(o)"),Gio.DBusCallFlags.NONE,30000,None,requested)
        def timeout():
            state["timeout"]=None
            if state["path"]:
                connection.call("org.freedesktop.portal.Desktop",state["path"],"org.freedesktop.portal.Request","Close",None,None,
                                Gio.DBusCallFlags.NONE,5000,None,None)
            finish(None,RuntimeError("Screenshot request timed out"));return False
        state["timeout"]=GLib.timeout_add_seconds(120,timeout)
    except Exception as error:finish(None,error)
