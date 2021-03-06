# Software License Agreement (BSD License)
#
# Copyright (c) 2012, Fraunhofer FKIE/US, Alexander Tiderko
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Fraunhofer nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import os, shlex, subprocess
import socket
import types
import time

import roslib
import rospy
import threading
import xmlrpclib

import node_manager_fkie as nm
from common import get_ros_home, masteruri_from_ros, package_name
try:
  from launch_config import LaunchConfig
except:
  pass


class StartException(Exception):
  pass

class StartHandler(object):
  '''
  This class contains the methods to run the nodes on local and remote machines
  in a screen terminal.
  '''
  def __init__(self):
    pass
  
  @classmethod
  def runNode(cls, node, launch_config, force2host=None, masteruri=None, auto_pw_request=False, user=None, pw=None):
    '''
    Start the node with given name from the given configuration.
    @param node: the name of the node (with name space)
    @type node: C{str}
    @param launch_config: the configuration containing the node
    @type launch_config: L{LaunchConfig} 
    @param force2host: start the node on given host.
    @type force2host: L{str} 
    @param masteruri: force the masteruri.
    @type masteruri: L{str} 
    @raise StartException: if the screen is not available on host.
    @raise Exception: on errors while resolving host
    @see: L{node_manager_fkie.is_local()}
    '''
    #'print "RUN node", node, time.time()
    n = launch_config.getNode(node)
    if n is None:
      raise StartException(''.join(["Node '", node, "' not found!"]))
    
    env = list(n.env_args)
    prefix = n.launch_prefix if not n.launch_prefix is None else ''
    if prefix.lower() == 'screen' or prefix.lower().find('screen ') != -1:
      rospy.loginfo("SCREEN prefix removed before start!")
      prefix = ''
    args = [''.join(['__ns:=', n.namespace]), ''.join(['__name:=', n.name])]
    if not (n.cwd is None):
      args.append(''.join(['__cwd:=', n.cwd]))
    
    # add remaps
    for remap in n.remap_args:
      args.append(''.join([remap[0], ':=', remap[1]]))

    # get host of the node
    host = launch_config.hostname
    env_loader = ''
    if n.machine_name:
      machine = launch_config.Roscfg.machines[n.machine_name]
      host = machine.address
      #TODO: env-loader support?
#      if hasattr(machine, "env_loader") and machine.env_loader:
#        env_loader = machine.env_loader
    # set the host to the given host
    if not force2host is None:
      host = force2host

    if masteruri is None:
      masteruri = nm.nameres().masteruri(n.machine_name)
    # set the ROS_MASTER_URI
    if masteruri is None:
      masteruri = masteruri_from_ros()
      env.append(('ROS_MASTER_URI', masteruri))

    abs_paths = list() # tuples of (parameter name, old value, new value)
    not_found_packages = list() # package names
    # set the global parameter
    if not masteruri is None and not masteruri in launch_config.global_param_done:
      global_node_names = cls.getGlobalParams(launch_config.Roscfg)
      rospy.loginfo("Register global parameter:\n%s", '\n'.join(global_node_names))
      abs_paths[len(abs_paths):], not_found_packages[len(not_found_packages):] = cls._load_parameters(masteruri, global_node_names, [])
      launch_config.global_param_done.append(masteruri)

    # add params
    if not masteruri is None:
      nodens = ''.join([n.namespace, n.name, '/'])
      params = dict()
      for param, value in launch_config.Roscfg.params.items():
        if param.startswith(nodens):
          params[param] = value
      clear_params = []
      for cparam in launch_config.Roscfg.clear_params:
        if cparam.startswith(nodens):
          clear_params.append(param)
      rospy.loginfo("Register parameter:\n%s", '\n'.join(params))
      abs_paths[len(abs_paths):], not_found_packages[len(not_found_packages):] = cls._load_parameters(masteruri, params, clear_params)
    #'print "RUN prepared", node, time.time()

    if nm.is_local(host): 
      nm.screen().testScreen()
      try:
        cmd = roslib.packages.find_node(n.package, n.type)
      except (Exception, roslib.packages.ROSPkgException) as e:
        # multiple nodes, invalid package
        raise StartException(''.join(["Can't find resource: ", str(e)]))
      # handle diferent result types str or array of string
      import types
      if isinstance(cmd, types.StringTypes):
        cmd = [cmd]
      cmd_type = ''
      if cmd is None or len(cmd) == 0:
        raise StartException(' '.join([n.type, 'in package [', n.package, '] not found!\n\nThe package was created?\nIs the binary executable?\n']))
      if len(cmd) > 1:
        # Open selection for executables
        try:
          from python_qt_binding import QtGui
          item, result = QtGui.QInputDialog.getItem(None, ' '.join(['Multiple executables', n.type, 'in', n.package]),
                                            'Select an executable',
                                            cmd, 0, False)
          if result:
            #open the selected screen
            cmd_type = item
          else:
            return
        except:
          raise StartException('Multiple executables with same name in package found!')
      else:
        cmd_type = cmd[0]
      # determine the current working path, Default: the package of the node
      cwd = get_ros_home()
      if not (n.cwd is None):
        if n.cwd == 'ROS_HOME':
          cwd = get_ros_home()
        elif n.cwd == 'node':
          cwd = os.path.dirname(cmd_type)
      cls._prepareROSMaster(masteruri)
      node_cmd = [nm.RESPAWN_SCRIPT if n.respawn else '', prefix, cmd_type]
      cmd_args = [nm.screen().getSceenCmd(node)]
      cmd_args[len(cmd_args):] = node_cmd
      cmd_args.append(str(n.args))
      cmd_args[len(cmd_args):] = args
      rospy.loginfo("RUN: %s", ' '.join(cmd_args))
      if not masteruri is None:
        new_env=dict(os.environ)
        new_env['ROS_MASTER_URI'] = masteruri
        ps = subprocess.Popen(shlex.split(str(' '.join(cmd_args))), cwd=cwd, env=new_env)
      else:
        ps = subprocess.Popen(shlex.split(str(' '.join(cmd_args))), cwd=cwd)
      # wait for process to avoid 'defunct' processes
      thread = threading.Thread(target=ps.wait)
      thread.setDaemon(True)
      thread.start()
    else:
      #'print "RUN REMOTE", node, time.time()
      # start remote
      if launch_config.PackageName is None:
        raise StartException(''.join(["Can't run remote without a valid package name!"]))
      # thus the prefix parameters while the transfer are not separated
      if prefix:
        prefix = ''.join(['"', prefix, '"'])
      # setup environment
      env_command = ''
      if env_loader:
        rospy.logwarn("env_loader in machine tag currently not supported")
        raise StartException("env_loader in machine tag currently not supported")
      if env:
        env_command = "env "+' '.join(["%s=%s"%(k,v) for (k, v) in env])
      
      startcmd = [env_command, nm.STARTER_SCRIPT, 
                  '--package', str(n.package),
                  '--node_type', str(n.type),
                  '--node_name', str(node),
                  '--node_respawn true' if n.respawn else '']
      if not masteruri is None:
        startcmd.append('--masteruri')
        startcmd.append(masteruri)
      if prefix:
        startcmd[len(startcmd):] = ['--prefix', prefix]
      
      #rename the absolute paths in the args of the node
      node_args = []
      try:
        for a in n.args.split():
          a_value, is_abs_path, found, package = cls._resolve_abs_paths(a, host)
          node_args.append(a_value)
          if is_abs_path:
            abs_paths.append(('ARGS', a, a_value))
            if not found and package:
              not_found_packages.append(package)
  
        startcmd[len(startcmd):] = node_args
        startcmd[len(startcmd):] = args
        rospy.loginfo("Run remote on %s: %s", host, str(' '.join(startcmd)))
        #'print "RUN CALL", node, time.time()
        output, error, ok = nm.ssh().ssh_exec(host, startcmd, user, pw, auto_pw_request)
        #'print "RUN CALLOK", node, time.time()
      except nm.AuthenticationRequest as e:
        raise nm.InteractionNeededError(e, cls.runNode, (node, launch_config, force2host, masteruri, auto_pw_request))

      if ok:
        if error:
          rospy.logwarn("ERROR while start '%s': %s", node, error)
          raise StartException(str(''.join(['The host "', host, '" reports:\n', error])))
        if output:
          rospy.loginfo("STDOUT while start '%s': %s", node, output)
      # inform about absolute paths in parameter value
      if len(abs_paths) > 0:
        rospy.loginfo("Absolute paths found while start:\n%s", str('\n'.join([''.join([p, '\n  OLD: ', ov, '\n  NEW: ', nv]) for p, ov, nv in abs_paths])))

      if len(not_found_packages) > 0:
        packages = '\n'.join(not_found_packages)
        raise StartException(str('\n'.join(['Some absolute paths are not renamed because following packages are not found on remote host:', packages])))
#      if len(abs_paths) > 0:
#        parameters = '\n'.join(abs_paths)
#        raise nm.StartException(str('\n'.join(['Following parameter seems to use an absolute local path for remote host:', parameters, 'Use "cwd" attribute of the "node" tag to specify relative paths for remote usage!'])))
    #'print "RUN OK", node, time.time()

  @classmethod
  def _load_parameters(cls, masteruri, params, clear_params):
    """
    Load parameters onto the parameter server
    """
    import roslaunch
    import roslaunch.launch
    import xmlrpclib
    param_server = xmlrpclib.ServerProxy(masteruri)
    p = None
    abs_paths = list() # tuples of (parameter name, old value, new value)
    not_found_packages = list() # pacakges names
    try:
      socket.setdefaulttimeout(6)
      # multi-call style xmlrpc
      param_server_multi = xmlrpclib.MultiCall(param_server)

      # clear specified parameter namespaces
      # #2468 unify clear params to prevent error
      for p in clear_params:
        param_server_multi.deleteParam(rospy.get_name(), p)
      r = param_server_multi()
#      for code, msg, _ in r:
#        if code != 1:
#          raise StartException("Failed to clear parameter: %s"%(msg))

      # multi-call objects are not reusable
      param_server_multi = xmlrpclib.MultiCall(param_server)
      for p in params.itervalues():
        # suppressing this as it causes too much spam
        value, is_abs_path, found, package = cls._resolve_abs_paths(p.value, nm.nameres().address(masteruri))
        if is_abs_path:
          abs_paths.append((p.key, p.value, value))
          if not found and package:
            not_found_packages.append(package)
        if p.value is None:
          raise StartException("The parameter '%s' is invalid!"%(p.value))
        param_server_multi.setParam(rospy.get_name(), p.key, value if is_abs_path else p.value)
      r  = param_server_multi()
      for code, msg, _ in r:
        if code != 1:
          raise StartException("Failed to set parameter: %s"%(msg))
    except roslaunch.core.RLException, e:
      raise StartException(e)
    except Exception as e:
      raise #re-raise as this is fatal
    finally:
      socket.setdefaulttimeout(None)
    return abs_paths, not_found_packages
  
  @classmethod
  def _resolve_abs_paths(cls, value, host):
    '''
    Replaces the local absolute path by remote absolute path. Only valid ROS
    package paths are resolved.
    @return: value, is absolute path, remote package found (ignore it on local host or if is not absolute path!), package name (if absolute path and remote package NOT found)
    '''
    if isinstance(value, types.StringTypes) and value.startswith('/') and (os.path.isfile(value) or os.path.isdir(value)):
      if nm.is_local(host):
        return value, True, True, ''
      else:
#        print "ABS PATH:", value, os.path.dirname(value)
        dir = os.path.dirname(value) if os.path.isfile(value) else value
        package, package_path = package_name(dir)
        if package:
          output, error, ok = nm.ssh().ssh_exec(host, ['rospack', 'find', package])
          if ok:
            if output:
#              print "  RESOLVED:", output
#              print "  PACK_PATH:", package_path
              value.replace(package_path, output)
#              print "  RENAMED:", value.replace(package_path, output.strip())
              return value.replace(package_path, output.strip()), True, True, package
            else:
              # package on remote host not found! 
              # TODO add error message
              #      error = stderr.read()
              pass
        return value, True, False, ''
    else:
      return value, False, False, ''

  @classmethod
  def runNodeWithoutConfig(cls, host, package, type, name, args=[], masteruri=None, auto_pw_request=True, user=None, pw=None):
    '''
    Start a node with using a launch configuration.
    @param host: the host or ip to run the node
    @type host: C{str} 
    @param package: the ROS package containing the binary
    @type package: C{str} 
    @param type: the binary of the node to execute
    @type type: C{str} 
    @param name: the ROS name of the node (with name space)
    @type name: C{str} 
    @param args: the list with arguments passed to the binary
    @type args: C{[str, ...]} 
    @raise Exception: on errors while resolving host
    @see: L{node_manager_fkie.is_local()}
    '''
    # create the name with namespace
    args2 = list(args)
    fullname = roslib.names.ns_join(roslib.names.SEP, name)
    for a in args:
      if a.startswith('__ns:='):
        fullname = roslib.names.ns_join(a.replace('__ns:=', ''), name)
    args2.append(''.join(['__name:=', name]))
    # run on local host
    if nm.is_local(host):
      try:
        cmd = roslib.packages.find_node(package, type)
      except roslib.packages.ROSPkgException as e:
        # multiple nodes, invalid package
        raise StartException(str(e))
      # handle different result types str or array of string
      import types
      if isinstance(cmd, types.StringTypes):
        cmd = [cmd]
      cmd_type = ''
      if cmd is None or len(cmd) == 0:
        raise StartException(' '.join([type, 'in package [', package, '] not found!']))
      if len(cmd) > 1:
        # Open selection for executables
#        try:
#          from python_qt_binding import QtGui
#          item, result = QtGui.QInputDialog.getItem(None, ' '.join(['Multiple executables', type, 'in', package]),
#                                            'Select an executable',
#                                            cmd, 0, False)
#          if result:
#            #open the selected screen
#            cmd_type = item
#          else:
#            return
#        except:
        err = [''.join(['Multiple executables with same name in package [', package, ']  found:'])]
        err.extend(cmd)
        raise StartException('\n'.join(err))
      else:
        cmd_type = cmd[0]
      cmd_str = str(' '.join([nm.screen().getSceenCmd(fullname), cmd_type, ' '.join(args2)]))
      rospy.loginfo("Run without config: %s", cmd_str)
      ps = None
      if not masteruri is None:
        cls._prepareROSMaster(masteruri)
        new_env=dict(os.environ)
        new_env['ROS_MASTER_URI'] = masteruri
        ps = subprocess.Popen(shlex.split(cmd_str), env=new_env)
      else:
        ps = subprocess.Popen(shlex.split(cmd_str))
      # wait for process to avoid 'defunct' processes
      thread = threading.Thread(target=ps.wait)
      thread.setDaemon(True)
      thread.start()
    else:
      # run on a remote machine
      startcmd = [nm.STARTER_SCRIPT, 
                  '--package', str(package),
                  '--node_type', str(type),
                  '--node_name', str(fullname)]
      startcmd[len(startcmd):] = args2
      if not masteruri is None:
        startcmd.append('--masteruri')
        startcmd.append(masteruri)
      rospy.loginfo("Run remote on %s: %s", host, ' '.join(startcmd))
      try:
        output, error, ok = nm.ssh().ssh_exec(host, startcmd, user, pw, auto_pw_request)
        if ok:
          if error:
            rospy.logwarn("ERROR while start '%s': %s", name, error)
            raise StartException(''.join(['The host "', host, '" reports:\n', error]))
  #          from python_qt_binding import QtGui
  #          QtGui.QMessageBox.warning(None, 'Error while remote start %s'%str(name),
  #                                      str(''.join(['The host "', host, '" reports:\n', error])),
  #                                      QtGui.QMessageBox.Ok)
          if output:
            rospy.logdebug("STDOUT while start '%s': %s", name, output)
        else:
          if error:
            rospy.logwarn("ERROR while start '%s': %s", name, error)
            raise StartException(''.join(['The host "', host, '" reports:\n', error]))
      except nm.AuthenticationRequest as e:
        raise nm.InteractionNeededError(e, cls.runNodeWithoutConfig, (host, package, type, name, args, masteruri, auto_pw_request))

  @classmethod
  def _prepareROSMaster(cls, masteruri):
    if not masteruri: 
      masteruri = roslib.rosenv.get_master_uri()
    #start roscore, if needed
    try:
      log_path = nm.ScreenHandler.LOG_PATH
      if not os.path.isdir(log_path):
        os.makedirs(log_path)
      socket.setdefaulttimeout(3)
      master = xmlrpclib.ServerProxy(masteruri)
      master.getUri(rospy.get_name())
    except:
#      socket.setdefaulttimeout(None)
#      import traceback
#      print traceback.format_exc()
      print "Start ROS-Master with", masteruri, "..."
      # run a roscore
      from urlparse import urlparse
      master_port = str(urlparse(masteruri).port)
      new_env = dict(os.environ)
      new_env['ROS_MASTER_URI'] = masteruri
      cmd_args = [nm.ScreenHandler.getSceenCmd(''.join(['/roscore', '--', master_port])), 'roscore', '--port', master_port]
      subprocess.Popen(shlex.split(' '.join([str(c) for c in cmd_args])), env=new_env)
      # wait for roscore to avoid connection problems while init_node
      result = -1
      count = 0
      while result == -1 and count < 3:
        try:
          print "  retry connect to ROS master"
          master = xmlrpclib.ServerProxy(masteruri)
          result, uri, msg = master.getUri(rospy.get_name())
        except:
          time.sleep(1)
          count += 1
      if count >= 3:
        raise StartException('Cannot connect to the ROS-Master: '+  str(masteruri))
    finally:
      socket.setdefaulttimeout(None)

    
  def callService(self, service_uri, service, service_type, service_args=[]):
    '''
    Calls the service and return the response.
    To call the service the ServiceProxy can't be used, because it uses 
    environment variables to determine the URI of the running service. In our 
    case this service can be running using another ROS master. The changes on the
    environment variables is not thread safe.
    So the source code of the rospy.SerivceProxy (tcpros_service.py) was modified.
    
    @param service_uri: the URI of the service
    @type service_uri: C{str}
    @param service: full service name (with name space)
    @type service: C{str}
    @param service_type: service class
    @type service_type: ServiceDefinition: service class
    @param args: arguments
    @return: the tuple of request and response.
    @rtype: C{(request object, response object)}
    @raise StartException: on error

    @see: L{rospy.SerivceProxy}

    '''
    rospy.loginfo("Call service %s[%s]: %s, %s", str(service), str(service_uri), str(service_type), str(service_args))
    from rospy.core import parse_rosrpc_uri, is_shutdown
    from rospy.msg import args_kwds_to_message
    from rospy.exceptions import TransportInitError, TransportException
    from rospy.impl.tcpros_base import TCPROSTransport, TCPROSTransportProtocol, DEFAULT_BUFF_SIZE
    from rospy.impl.tcpros_service import TCPROSServiceClient
    from rospy.service import ServiceException
    request = service_type._request_class()
    import genpy
    try:
      now = rospy.get_rostime() 
      import std_msgs.msg
      keys = { 'now': now, 'auto': std_msgs.msg.Header(stamp=now) }
      genpy.message.fill_message_args(request, service_args, keys)
    except genpy.MessageException as e:
        def argsummary(args):
            if type(args) in [tuple, list]:
                return '\n'.join([' * %s (type %s)'%(a, type(a).__name__) for a in args])
            else:
                return ' * %s (type %s)'%(args, type(args).__name__)
        raise StartException("Incompatible arguments to call service:\n%s\nProvided arguments are:\n%s\n\nService arguments are: [%s]"%(e, argsummary(service_args), genpy.message.get_printable_message_args(request)))

#    request = args_kwds_to_message(type._request_class, args, kwds) 
    transport = None
    protocol = TCPROSServiceClient(service, service_type, headers={})
    transport = TCPROSTransport(protocol, service)
    # initialize transport
    dest_addr, dest_port = parse_rosrpc_uri(service_uri)

    # connect to service            
    transport.buff_size = DEFAULT_BUFF_SIZE
    try:
      transport.connect(dest_addr, dest_port, service_uri, timeout=5)
    except TransportInitError as e:
      # can be a connection or md5sum mismatch
      raise StartException(''.join(["unable to connect to service: ", str(e)]))
    transport.send_message(request, 0)
    try:
      responses = transport.receive_once()
      if len(responses) == 0:
        raise StartException("service [%s] returned no response"%service)
      elif len(responses) > 1:
        raise StartException("service [%s] returned multiple responses: %s"%(service, len(responses)))
    except TransportException as e:
      # convert lower-level exception to exposed type
      if is_shutdown():
        raise StartException("node shutdown interrupted service call")
      else:
        raise StartException("transport error completing service call: %s"%(str(e)))
    except ServiceException, e:
      raise StartException("Service error: %s"%(str(e)))
    finally:
      transport.close()
      transport = None
    return request, responses[0] if len(responses) > 0 else None


  @classmethod
  def getGlobalParams(cls, roscfg):
    '''
    Return the parameter of the configuration file, which are not associated with 
    any nodes in the configuration.
    @param roscfg: the launch configuration
    @type roscfg: L{roslaunch.ROSLaunchConfig}
    @return: the list with names of the global parameter
    @rtype: C{dict(param:value, ...)}
    '''
    result = dict()
    nodes = []
    for item in roscfg.resolved_node_names:
      nodes.append(item)
    for param, value in roscfg.params.items():
      nodesparam = False
      for n in nodes:
        if param.startswith(n):
          nodesparam = True
          break
      if not nodesparam:
        result[param] = value
    return result

  @classmethod
  def copylogPath2Clipboards(self, host, nodes=[], auto_pw_request=True, user=None, pw=None):
    if nm.is_local(host):
      if len(nodes) == 1:
        return nm.screen().getScreenLogFile(node=nodes[0])
      else:
        return nm.screen().LOG_PATH
    else:
      request = '[]' if len(nodes) != 1 else nodes[0]
      try:
        output, error, ok = nm.ssh().ssh_exec(host, [nm.STARTER_SCRIPT, '--ros_log_path', request], user, pw, auto_pw_request)
        if ok:
          return output
        else:
          raise StartException(str(''.join(['Get log path from "', host, '" failed:\n', error])))
      except nm.AuthenticationRequest as e:
        raise nm.InteractionNeededError(e, cls.deleteLog, (nodename, host, auto_pw_request))

  @classmethod
  def openLog(cls, nodename, host):
    '''
    Opens the log file associated with the given node in a new terminal.
    @param nodename: the name of the node (with name space)
    @type nodename: C{str}
    @param host: the host name or ip where the log file are
    @type host: C{str}
    @return: C{True}, if a log file was found
    @rtype: C{bool}
    @raise Exception: on errors while resolving host
    @see: L{node_manager_fkie.is_local()}
    '''
    rospy.loginfo("show log for '%s' on '%s'", str(nodename), str(host))
    title_opt = ' '.join(['"LOG', nodename, 'on', host, '"'])
    if nm.is_local(host):
      found = False
      screenLog = nm.screen().getScreenLogFile(node=nodename)
      if os.path.isfile(screenLog):
        cmd = nm.terminal_cmd([nm.LESS, screenLog], title_opt)
        rospy.loginfo("open log: %s", cmd)
        ps = subprocess.Popen(shlex.split(cmd))
        # wait for process to avoid 'defunct' processes
        thread = threading.Thread(target=ps.wait)
        thread.setDaemon(True)
        thread.start()
        found = True
      #open roslog file
      roslog = nm.screen().getROSLogFile(nodename)
      if os.path.isfile(roslog):
        title_opt = title_opt.replace('LOG', 'ROSLOG')
        cmd = nm.terminal_cmd([nm.LESS, roslog], title_opt)
        rospy.loginfo("open ROS log: %s", cmd)
        ps = subprocess.Popen(shlex.split(cmd))
        # wait for process to avoid 'defunct' processes
        thread = threading.Thread(target=ps.wait)
        thread.setDaemon(True)
        thread.start()
        found = True
      return found
    else:
      ps = nm.ssh().ssh_x11_exec(host, [nm.STARTER_SCRIPT, '--show_screen_log', nodename], title_opt)
      # wait for process to avoid 'defunct' processes
      thread = threading.Thread(target=ps.wait)
      thread.setDaemon(True)
      thread.start()
      ps = nm.ssh().ssh_x11_exec(host, [nm.STARTER_SCRIPT, '--show_ros_log', nodename], title_opt.replace('LOG', 'ROSLOG'))
      # wait for process to avoid 'defunct' processes
      thread = threading.Thread(target=ps.wait)
      thread.setDaemon(True)
      thread.start()
    return False


  @classmethod
  def deleteLog(cls, nodename, host, auto_pw_request=True, user=None, pw=None):
    '''
    Deletes the log file associated with the given node.
    @param nodename: the name of the node (with name space)
    @type nodename: C{str}
    @param host: the host name or ip where the log file are to delete
    @type host: C{str}
    @raise Exception: on errors while resolving host
    @see: L{node_manager_fkie.is_local()}
    '''
    rospy.loginfo("delete log for '%s' on '%s'", str(nodename), str(host))
    if nm.is_local(host):
      screenLog = nm.screen().getScreenLogFile(node=nodename)
      pidFile = nm.screen().getScreenPidFile(node=nodename)
      roslog = nm.screen().getROSLogFile(nodename)
      if os.path.isfile(screenLog):
        os.remove(screenLog)
      if os.path.isfile(pidFile):
        os.remove(pidFile)
      if os.path.isfile(roslog):
        os.remove(roslog)
    else:
      try:
        output, error, ok = nm.ssh().ssh_exec(host, [nm.STARTER_SCRIPT, '--delete_logs', nodename], user, pw, auto_pw_request)
      except nm.AuthenticationRequest as e:
        raise nm.InteractionNeededError(e, cls.deleteLog, (nodename, host, auto_pw_request))

  def kill(self, host, pid, auto_pw_request=True, user=None, pw=None):
    '''
    Kills the process with given process id on given host.
    @param host: the name or address of the host, where the process must be killed.
    @type host: C{str}
    @param pid: the process id
    @type pid: C{int}
    @raise StartException: on error
    @raise Exception: on errors while resolving host
    @see: L{node_manager_fkie.is_local()}
    '''
    try:
      self._kill_wo(host, pid, auto_pw_request, user, pw)
    except nm.AuthenticationRequest as e:
      raise nm.InteractionNeededError(e, cls.deleteLog, (nodename, host, auto_pw_request))

  def _kill_wo(self, host, pid, auto_pw_request=True, user=None, pw=None):
    rospy.loginfo("kill %s on %s", str(pid), host)
    if nm.is_local(host): 
      import signal
      os.kill(pid, signal.SIGKILL)
      rospy.loginfo("kill: %s", str(pid))
    else:
      # kill on a remote machine
      cmd = ['kill -9', str(pid)]
      output, error, ok = nm.ssh().ssh_exec(host, cmd, user, pw, False)
      if ok:
        if error:
          rospy.logwarn("ERROR while kill %s: %s", str(pid), error)
          raise StartException(str(''.join(['The host "', host, '" reports:\n', error])))
        if output:
          rospy.logdebug("STDOUT while kill %s on %s: %s", str(pid), host, output)
