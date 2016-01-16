import logging
import os
from six.moves.urllib.parse import unquote
from aatest import exception_trace
from aatest import Break
from aatest.io import IO, eval_state

from oic.utils.http_util import Response, NotFound
from oic.utils.time_util import in_a_while

from aatest.check import ERROR, State
from aatest.check import OK
from aatest.check import WARNING
from aatest.check import INCOMPLETE
from aatest.summation import represent_result
from aatest.summation import evaluate
from aatest.summation import condition
from aatest.summation import trace_output
from aatest.summation import create_tar_archive

from oidctest.utils import get_profile_info, get_check
from oidctest.utils import log_path
from oidctest.utils import with_or_without_slash
from oidctest.utils import get_test_info

__author__ = 'roland'

logger = logging.getLogger(__name__)

TEST_RESULTS = {OK: "OK", ERROR: "ERROR", WARNING: "WARNING",
                INCOMPLETE: "INCOMPLETE"}


# class IO(object):
#     def __init__(self, conf, flows, profile, profiles, operation,
#                  desc, cache=None):
#         self.conf = conf
#         self.flows = flows
#         self.cache = cache
#         self.test_profile = profile
#         self.profiles = profiles
#         self.operation = operation
#         self.check_factory = get_check
#         self.desc = desc
#
#     def dump_log(self, session, test_id):
#         pass
#
#     def err_response(self, session, where, err):
#         pass


class WebIO(IO):
    def __init__(self, conf=None, flows=None, profile='', desc=None,
                 lookup=None, check_factory=None, cache=None, environ=None,
                 start_response=None, **kwargs):
        IO.__init__(self, flows=flows, profile=profile, desc=desc,
                    check_factory=check_factory)
        self.conf = conf
        self.lookup = lookup
        self.environ = environ
        self.start_response = start_response
        self.cache = cache
        self.kwargs = kwargs

    @staticmethod
    def store_test_info(session, profile_info=None):
        _conv = session["conv"]
        _info = {
            "trace": _conv.trace,
            "events": _conv.events,
            "index": session["index"],
            "seqlen": len(session["sequence"]),
            "descr": session["node"].desc
        }

        try:
            _info["node"] = session["node"]
        except KeyError:
            pass

        if profile_info:
            _info["profile_info"] = profile_info
        else:
            try:
                _info["profile_info"] = get_profile_info(session,
                                                         session["testid"])
            except KeyError:
                pass

        session["test_info"][session["testid"]] = _info

    def flow_list(self, session):
        resp = Response(mako_template="flowlist.mako",
                        template_lookup=self.lookup,
                        headers=[])

        try:
            _tid = session["testid"]
        except KeyError:
            _tid = None

        self.dump_log(session, _tid)

        argv = {
            "flows": session["tests"],
            "profile": session["profile"],
            "test_info": list(session["test_info"].keys()),
            "base": self.conf.BASE,
            "headlines": self.desc,
            "testresults": TEST_RESULTS
        }

        return resp(self.environ, self.start_response, **argv)

    def dump_log(self, session, test_id=None):
        try:
            _conv = session["conv"]
        except KeyError:
            pass
        else:
            _pi = get_profile_info(session, test_id)
            if _pi:
                _tid = _pi["Test ID"]
                path = log_path(session, _tid)
                if not path:
                    return

                sline = 60 * "="
                output = ["%s: %s" % (k, _pi[k]) for k in ["Issuer", "Profile",
                                                           "Test ID"]]
                output.append("Timestamp: %s" % in_a_while())
                output.extend(["", sline, ""])
                output.extend(trace_output(_conv.trace))
                output.extend(["", sline, ""])
                output.extend(condition(_conv.events))
                output.extend(["", sline, ""])
                # and lastly the result
                self.store_test_info(session, _pi)
                _info = session["test_info"][_tid]
                output.append("RESULT: %s" % represent_result(
                    _info, eval_state(_conv.events)))
                output.append("")

                f = open(path, "w")
                txt = "\n".join(output)

                try:
                    f.write(txt)
                except UnicodeEncodeError:
                    f.write(txt.encode("utf8"))

                f.close()
                pp = path.split("/")
                create_tar_archive(pp[1], pp[2])
                return path

    def profile_edit(self, session):
        resp = Response(mako_template="profile.mako",
                        template_lookup=self.lookup,
                        headers=[])
        argv = {"profile": session["profile"]}
        return resp(self.environ, self.start_response, **argv)

    def test_info(self, testid, session):
        resp = Response(mako_template="testinfo.mako",
                        template_lookup=self.lookup,
                        headers=[])

        info = get_test_info(session, testid)

        argv = {
            "profile": info["profile_info"],
            "trace": info["trace"],
            "events": info["events"],
            "result": represent_result(
                info, eval_state(session['conv'].events)).replace("\n", "<br>\n")
        }

        logger.debug(argv)

        return resp(self.environ, self.start_response, **argv)

    def not_found(self):
        """Called if no URL matches."""
        resp = NotFound()
        return resp(self.environ, self.start_response)

    def static(self, path):
        logger.info("[static]sending: %s" % (path,))

        try:
            text = open(path, 'rb').read()
            if path.endswith(".ico"):
                self.start_response('200 OK', [('Content-Type',
                                                "image/x-icon")])
            elif path.endswith(".html"):
                self.start_response('200 OK', [('Content-Type', 'text/html')])
            elif path.endswith(".json"):
                self.start_response('200 OK', [('Content-Type',
                                                'application/json')])
            elif path.endswith(".jwt"):
                self.start_response('200 OK', [('Content-Type',
                                                'application/jwt')])
            elif path.endswith(".txt"):
                self.start_response('200 OK', [('Content-Type', 'text/plain')])
            elif path.endswith(".css"):
                self.start_response('200 OK', [('Content-Type', 'text/css')])
            else:
                self.start_response('200 OK', [('Content-Type', "text/plain")])
            return [text]
        except IOError:
            resp = NotFound()
            return resp(self.environ, self.start_response)

    def _display(self, root, issuer, profile):
        item = []
        if profile:
            path = os.path.join(root, issuer, profile).replace(":", "%3A")
            argv = {"issuer": unquote(issuer), "profile": profile}

            path = with_or_without_slash(path)
            if path is None:
                resp = Response("No saved logs")
                return resp(self.environ, self.start_response)

            for _name in os.listdir(path):
                if _name.startswith("."):
                    continue
                fn = os.path.join(path, _name)
                if os.path.isfile(fn):
                    item.append((unquote(_name), os.path.join(profile, _name)))
        else:
            if issuer:
                argv = {'issuer': unquote(issuer), 'profile': ''}
                path = os.path.join(root, issuer).replace(":", "%3A")
            else:
                argv = {'issuer': '', 'profile': ''}
                path = root

            path = with_or_without_slash(path)
            if path is None:
                resp = Response("No saved logs")
                return resp(self.environ, self.start_response)

            for _name in os.listdir(path):
                if _name.startswith("."):
                    continue
                fn = os.path.join(path, _name)
                if os.path.isdir(fn):
                    item.append((unquote(_name), os.path.join(path, _name)))

        resp = Response(mako_template="logs.mako",
                        template_lookup=self.lookup,
                        headers=[])

        item.sort()
        argv["logs"] = item
        return resp(self.environ, self.start_response, **argv)

    def display_log(self, root, issuer="", profile="", testid=""):
        logger.info(
            "display_log root: '%s' issuer: '%s', profile: '%s' testid: '%s'"
            % (
                root, issuer, profile, testid))
        if testid:
            path = os.path.join(root, issuer, profile, testid).replace(":",
                                                                       "%3A")
            return self.static(path)
        else:
            if issuer:
                return self._display(root, issuer, profile)
            else:
                resp = Response("No saved logs")
                return resp(self.environ, self.start_response)

    @staticmethod
    def get_err_type(session):
        errt = WARNING
        try:
            if session["node"].mti == {"all": "MUST"}:
                errt = ERROR
        except KeyError:
            pass
        return errt

    def log_fault(self, session, err, where, err_type=0):
        if err_type == 0:
            err_type = self.get_err_type(session)

        if "node" in session:
            if err:
                if isinstance(err, Break):
                    session["node"].state = WARNING
                else:
                    session["node"].state = err_type
            else:
                session["node"].state = err_type

        if "conv" in session:
            if err:
                if isinstance(err, str):
                    pass
                else:
                    session["conv"].trace.error("%s:%s" % (
                        err.__class__.__name__, str(err)))
                session["conv"].events.store(
                    'fault', State(test_id="-", status=err_type,
                                   message="{}".format(err)))
            else:
                session["conv"].events.store(
                    'fault', State(test_id="-", status=err_type,
                                   message="Error in {}".format(where)))

    def err_response(self, session, where, err):
        if err:
            exception_trace(where, err, logger)

        self.log_fault(session, err, where)

        try:
            _tid = session["testid"]
            self.dump_log(session, _tid)
            self.store_test_info(session)
        except KeyError:
            pass

        return self.flow_list(session)

    def sorry_response(self, homepage, err):
        resp = Response(mako_template="sorry.mako",
                        template_lookup=self.lookup,
                        headers=[])
        argv = {"htmlpage": homepage,
                "error": str(err)}
        return resp(self.environ, self.start_response, **argv)

    def opresult(self, conv, session):
        evaluate(session, session["test_info"][conv.test_id])
        return self.flow_list(session)

    def opresult_fragment(self):
        resp = Response(mako_template="opresult_repost.mako",
                        template_lookup=self.lookup,
                        headers=[])
        argv = {}
        return resp(self.environ, self.start_response, **argv)

    def respond(self, resp):
        if isinstance(resp, Response):
            return resp(self.environ, self.start_response)
        else:
            return resp


SIGN = {OK: "+", WARNING: "!", ERROR: "-", INCOMPLETE: "?"}


class ClIO(IO):
    def flow_list(self, session):
        pass

    def dump_log(self, session, test_id):
        try:
            _conv = session["conv"]
        except KeyError:
            pass
        else:
            _pi = get_profile_info(session, test_id)
            if _pi:
                sline = 60 * "="
                output = ["%s: %s" % (k, _pi[k]) for k in ["Issuer", "Profile",
                                                           "Test ID"]]
                output.append("Timestamp: %s" % in_a_while())
                output.extend(["", sline, ""])
                output.extend(trace_output(_conv.trace))
                output.extend(["", sline, ""])
                output.extend(condition(_conv.events))
                output.extend(["", sline, ""])
                # and lastly the result
                info = {
                    "events": _conv.events,
                    "trace": _conv.trace
                }
                output.append("RESULT: %s" % represent_result(
                    info, eval_state(_conv.events)))
                output.append("")

                txt = "\n".join(output)

                print(txt)

    def result(self, session):
        _conv = session["conv"]
        info = {
            "events": _conv.events,
            "trace": _conv.trace
        }
        _state = evaluate(session, info)
        print("{} {}".format(SIGN[_state], session["node"].name))

    def err_response(self, session, where, err):
        if err:
            exception_trace(where, err, logger)

        try:
            _tid = session["testid"]
            self.dump_log(session, _tid)
        except KeyError:
            pass
