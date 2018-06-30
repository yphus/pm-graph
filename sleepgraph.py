#!/usr/bin/python2
#
# Tool for analyzing suspend/resume timing
# Copyright (c) 2013, Intel Corporation.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms and conditions of the GNU General Public License,
# version 2, as published by the Free Software Foundation.
#
# This program is distributed in the hope it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# Authors:
#	 Todd Brandt <todd.e.brandt@linux.intel.com>
#
# Links:
#	 Home Page
#	   https://01.org/suspendresume
#	 Source repo
#	   git@github.com:01org/pm-graph
#
# Description:
#	 This tool is designed to assist kernel and OS developers in optimizing
#	 their linux stack's suspend/resume time. Using a kernel image built
#	 with a few extra options enabled, the tool will execute a suspend and
#	 will capture dmesg and ftrace data until resume is complete. This data
#	 is transformed into a device timeline and a callgraph to give a quick
#	 and detailed view of which devices and callbacks are taking the most
#	 time in suspend/resume. The output is a single html file which can be
#	 viewed in firefox or chrome.
#
#	 The following kernel build options are required:
#		 CONFIG_PM_DEBUG=y
#		 CONFIG_PM_SLEEP_DEBUG=y
#		 CONFIG_FTRACE=y
#		 CONFIG_FUNCTION_TRACER=y
#		 CONFIG_FUNCTION_GRAPH_TRACER=y
#		 CONFIG_KPROBES=y
#		 CONFIG_KPROBES_ON_FTRACE=y
#
#	 For kernel versions older than 3.15:
#	 The following additional kernel parameters are required:
#		 (e.g. in file /etc/default/grub)
#		 GRUB_CMDLINE_LINUX_DEFAULT="... initcall_debug log_buf_len=16M ..."
#

# ----------------- LIBRARIES --------------------

import sys
import time
import os
import string
import re
import platform
from datetime import datetime
import struct
import ConfigParser
import gzip
from threading import Thread
from subprocess import call, Popen, PIPE
import base64
import json
import requests
import urllib

# ----------------- CLASSES --------------------

# Class: SystemValues
# Description:
#	 A global, single-instance container used to
#	 store system values and test parameters
class SystemValues:
	title = 'SleepGraph'
	version = '5.1'
	component = 'sleepgraph'
	ansi = False
	rs = 0
	display = 0
	gzip = False
	sync = False
	verbose = False
	testlog = True
	dmesglog = False
	ftracelog = False
	mindevlen = 0.0
	mincglen = 0.0
	cgphase = ''
	cgtest = -1
	cgskip = ''
	multitest = {'run': False, 'count': 0, 'delay': 0}
	max_graph_depth = 0
	callloopmaxgap = 0.0001
	callloopmaxlen = 0.005
	bufsize = 0
	cpucount = 0
	memtotal = 204800
	memfree = 204800
	srgap = 0
	cgexp = False
	testdir = ''
	outdir = ''
	tpath = '/sys/kernel/debug/tracing/'
	fpdtpath = '/sys/firmware/acpi/tables/FPDT'
	epath = '/sys/kernel/debug/tracing/events/power/'
	traceevents = [
		'suspend_resume',
		'device_pm_callback_end',
		'device_pm_callback_start'
	]
	logmsg = ''
	testcommand = ''
	mempath = '/dev/mem'
	powerfile = '/sys/power/state'
	mempowerfile = '/sys/power/mem_sleep'
	suspendmode = 'mem'
	memmode = ''
	hostname = 'localhost'
	prefix = 'test'
	teststamp = ''
	sysstamp = ''
	dmesgstart = 0.0
	dmesgfile = ''
	ftracefile = ''
	htmlfile = 'output.html'
	extra = ''
	result = ''
	rtcwake = True
	rtcwaketime = 15
	rtcpath = ''
	devicefilter = []
	cgfilter = []
	stamp = 0
	execcount = 1
	x2delay = 0
	skiphtml = False
	usecallgraph = False
	usetraceevents = False
	usetracemarkers = True
	usekprobes = True
	usedevsrc = False
	useprocmon = False
	notestrun = False
	cgdump = False
	mixedphaseheight = True
	devprops = dict()
	predelay = 0
	postdelay = 0
	tracefuncs = {
		'sys_sync': {},
		'__pm_notifier_call_chain': {},
		'pm_prepare_console': {},
		'pm_notifier_call_chain': {},
		'freeze_processes': {},
		'freeze_kernel_threads': {},
		'pm_restrict_gfp_mask': {},
		'acpi_suspend_begin': {},
		'acpi_hibernation_begin': {},
		'acpi_hibernation_enter': {},
		'acpi_hibernation_leave': {},
		'acpi_pm_freeze': {},
		'acpi_pm_thaw': {},
		'hibernate_preallocate_memory': {},
		'create_basic_memory_bitmaps': {},
		'swsusp_write': {},
		'suspend_console': {},
		'acpi_pm_prepare': {},
		'syscore_suspend': {},
		'arch_enable_nonboot_cpus_end': {},
		'syscore_resume': {},
		'acpi_pm_finish': {},
		'resume_console': {},
		'acpi_pm_end': {},
		'pm_restore_gfp_mask': {},
		'thaw_processes': {},
		'pm_restore_console': {},
		'CPU_OFF': {
			'func':'_cpu_down',
			'args_x86_64': {'cpu':'%di:s32'},
			'format': 'CPU_OFF[{cpu}]'
		},
		'CPU_ON': {
			'func':'_cpu_up',
			'args_x86_64': {'cpu':'%di:s32'},
			'format': 'CPU_ON[{cpu}]'
		},
	}
	dev_tracefuncs = {
		# general wait/delay/sleep
		'msleep': { 'args_x86_64': {'time':'%di:s32'}, 'ub': 1 },
		'schedule_timeout_uninterruptible': { 'args_x86_64': {'timeout':'%di:s32'}, 'ub': 1 },
		'schedule_timeout': { 'args_x86_64': {'timeout':'%di:s32'}, 'ub': 1 },
		'udelay': { 'func':'__const_udelay', 'args_x86_64': {'loops':'%di:s32'}, 'ub': 1 },
		'usleep_range': { 'args_x86_64': {'min':'%di:s32', 'max':'%si:s32'}, 'ub': 1 },
		'mutex_lock_slowpath': { 'func':'__mutex_lock_slowpath', 'ub': 1 },
		'acpi_os_stall': {'ub': 1},
		# ACPI
		'acpi_resume_power_resources': {},
		'acpi_ps_parse_aml': {},
		# filesystem
		'ext4_sync_fs': {},
		# 80211
		'iwlagn_mac_start': {},
		'iwlagn_alloc_bcast_station': {},
		'iwl_trans_pcie_start_hw': {},
		'iwl_trans_pcie_start_fw': {},
		'iwl_run_init_ucode': {},
		'iwl_load_ucode_wait_alive': {},
		'iwl_alive_start': {},
		'iwlagn_mac_stop': {},
		'iwlagn_mac_suspend': {},
		'iwlagn_mac_resume': {},
		'iwlagn_mac_add_interface': {},
		'iwlagn_mac_remove_interface': {},
		'iwlagn_mac_change_interface': {},
		'iwlagn_mac_config': {},
		'iwlagn_configure_filter': {},
		'iwlagn_mac_hw_scan': {},
		'iwlagn_bss_info_changed': {},
		'iwlagn_mac_channel_switch': {},
		'iwlagn_mac_flush': {},
		# ATA
		'ata_eh_recover': { 'args_x86_64': {'port':'+36(%di):s32'} },
		# i915
		'i915_gem_resume': {},
		'i915_restore_state': {},
		'intel_opregion_setup': {},
		'g4x_pre_enable_dp': {},
		'vlv_pre_enable_dp': {},
		'chv_pre_enable_dp': {},
		'g4x_enable_dp': {},
		'vlv_enable_dp': {},
		'intel_hpd_init': {},
		'intel_opregion_register': {},
		'intel_dp_detect': {},
		'intel_hdmi_detect': {},
		'intel_opregion_init': {},
		'intel_fbdev_set_suspend': {},
	}
	cgblacklist = []
	kprobes = dict()
	timeformat = '%.3f'
	cmdline = '%s %s' % \
			(os.path.basename(sys.argv[0]), ' '.join(sys.argv[1:]))
	def __init__(self):
		self.archargs = 'args_'+platform.machine()
		self.hostname = platform.node()
		if(self.hostname == ''):
			self.hostname = 'localhost'
		rtc = "rtc0"
		if os.path.exists('/dev/rtc'):
			rtc = os.readlink('/dev/rtc')
		rtc = '/sys/class/rtc/'+rtc
		if os.path.exists(rtc) and os.path.exists(rtc+'/date') and \
			os.path.exists(rtc+'/time') and os.path.exists(rtc+'/wakealarm'):
			self.rtcpath = rtc
		if (hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()):
			self.ansi = True
		self.testdir = datetime.now().strftime('suspend-%y%m%d-%H%M%S')
	def vprint(self, msg):
		self.logmsg += msg+'\n'
		if(self.verbose):
			print(msg)
	def rootCheck(self, fatal=True):
		if(os.access(self.powerfile, os.W_OK)):
			return True
		if fatal:
			msg = 'This command requires sysfs mount and root access'
			print('ERROR: %s\n') % msg
			self.outputResult({'error':msg})
			sys.exit(1)
		return False
	def rootUser(self, fatal=False):
		if 'USER' in os.environ and os.environ['USER'] == 'root':
			return True
		if fatal:
			msg = 'This command must be run as root'
			print('ERROR: %s\n') % msg
			self.outputResult({'error':msg})
			sys.exit(1)
		return False
	def getExec(self, cmd):
		dirlist = ['/sbin', '/bin', '/usr/sbin', '/usr/bin',
			'/usr/local/sbin', '/usr/local/bin']
		for path in dirlist:
			cmdfull = os.path.join(path, cmd)
			if os.path.exists(cmdfull):
				return cmdfull
		return ''
	def setPrecision(self, num):
		if num < 0 or num > 6:
			return
		self.timeformat = '%.{0}f'.format(num)
	def setOutputFolder(self, value):
		args = dict()
		n = datetime.now()
		args['date'] = n.strftime('%y%m%d')
		args['time'] = n.strftime('%H%M%S')
		args['hostname'] = args['host'] = self.hostname
		return value.format(**args)
	def setOutputFile(self):
		if self.dmesgfile != '':
			m = re.match('(?P<name>.*)_dmesg\.txt.*', self.dmesgfile)
			if(m):
				self.htmlfile = m.group('name')+'.html'
		if self.ftracefile != '':
			m = re.match('(?P<name>.*)_ftrace\.txt.*', self.ftracefile)
			if(m):
				self.htmlfile = m.group('name')+'.html'
	def systemInfo(self, info):
		p = c = m = b = ''
		if 'baseboard-manufacturer' in info:
			m = info['baseboard-manufacturer']
		elif 'system-manufacturer' in info:
			m = info['system-manufacturer']
		if 'baseboard-product-name' in info:
			p = info['baseboard-product-name']
		elif 'system-product-name' in info:
			p = info['system-product-name']
		if 'processor-version' in info:
			c = info['processor-version']
		if 'bios-version' in info:
			b = info['bios-version']
		self.sysstamp = '# sysinfo | man:%s | plat:%s | cpu:%s | bios:%s | numcpu:%d | memsz:%d | memfr:%d' % \
			(m, p, c, b, self.cpucount, self.memtotal, self.memfree)
	def printSystemInfo(self, fatal=False):
		self.rootCheck(True)
		out = dmidecode(self.mempath, fatal)
		if len(out) < 1:
			return
		fmt = '%-24s: %s'
		for name in sorted(out):
			print fmt % (name, out[name])
		print fmt % ('cpucount', ('%d' % self.cpucount))
		print fmt % ('memtotal', ('%d kB' % self.memtotal))
		print fmt % ('memfree', ('%d kB' % self.memfree))
	def cpuInfo(self):
		self.cpucount = 0
		fp = open('/proc/cpuinfo', 'r')
		for line in fp:
			if re.match('^processor[ \t]*:[ \t]*[0-9]*', line):
				self.cpucount += 1
		fp.close()
		fp = open('/proc/meminfo', 'r')
		for line in fp:
			m = re.match('^MemTotal:[ \t]*(?P<sz>[0-9]*) *kB', line)
			if m:
				self.memtotal = int(m.group('sz'))
			m = re.match('^MemFree:[ \t]*(?P<sz>[0-9]*) *kB', line)
			if m:
				self.memfree = int(m.group('sz'))
		fp.close()
	def initTestOutput(self, name):
		self.prefix = self.hostname
		v = open('/proc/version', 'r').read().strip()
		kver = string.split(v)[2]
		fmt = name+'-%m%d%y-%H%M%S'
		testtime = datetime.now().strftime(fmt)
		self.teststamp = \
			'# '+testtime+' '+self.prefix+' '+self.suspendmode+' '+kver
		ext = ''
		if self.gzip:
			ext = '.gz'
		self.dmesgfile = \
			self.testdir+'/'+self.prefix+'_'+self.suspendmode+'_dmesg.txt'+ext
		self.ftracefile = \
			self.testdir+'/'+self.prefix+'_'+self.suspendmode+'_ftrace.txt'+ext
		self.htmlfile = \
			self.testdir+'/'+self.prefix+'_'+self.suspendmode+'.html'
		if not os.path.isdir(self.testdir):
			os.mkdir(self.testdir)
	def submitOptions(self):
		self.ftracelog = False
		self.dmesglog = True
		self.useprocmon = False
		self.testlog = True
		if not self.extra:
			self.usecallgraph = False
			self.usedevsrc = False
			self.timeformat = '%.6f'
			self.mindevlen = 0.0
			self.srgap = 0
			self.ftracelog = True
		else:
			if self.extra == 'dev':
				self.usedevsrc = True
			elif self.extra == 'callgraph':
				self.usecallgraph = True
	def getValueList(self, value):
		out = []
		for i in value.split(','):
			if i.strip():
				out.append(i.strip())
		return out
	def setDeviceFilter(self, value):
		self.devicefilter = self.getValueList(value)
	def setCallgraphFilter(self, value):
		self.cgfilter = self.getValueList(value)
	def setCallgraphBlacklist(self, file):
		self.cgblacklist = self.listFromFile(file)
	def rtcWakeAlarmOn(self):
		call('echo 0 > '+self.rtcpath+'/wakealarm', shell=True)
		nowtime = open(self.rtcpath+'/since_epoch', 'r').read().strip()
		if nowtime:
			nowtime = int(nowtime)
		else:
			# if hardware time fails, use the software time
			nowtime = int(datetime.now().strftime('%s'))
		alarm = nowtime + self.rtcwaketime
		call('echo %d > %s/wakealarm' % (alarm, self.rtcpath), shell=True)
	def rtcWakeAlarmOff(self):
		call('echo 0 > %s/wakealarm' % self.rtcpath, shell=True)
	def initdmesg(self):
		# get the latest time stamp from the dmesg log
		fp = Popen('dmesg', stdout=PIPE).stdout
		ktime = '0'
		for line in fp:
			line = line.replace('\r\n', '')
			idx = line.find('[')
			if idx > 1:
				line = line[idx:]
			m = re.match('[ \t]*(\[ *)(?P<ktime>[0-9\.]*)(\]) (?P<msg>.*)', line)
			if(m):
				ktime = m.group('ktime')
		fp.close()
		self.dmesgstart = float(ktime)
	def getdmesg(self, fwdata, bdata):
		op = self.writeDatafileHeader(sysvals.dmesgfile, fwdata, bdata)
		# store all new dmesg lines since initdmesg was called
		fp = Popen('dmesg', stdout=PIPE).stdout
		for line in fp:
			line = line.replace('\r\n', '')
			idx = line.find('[')
			if idx > 1:
				line = line[idx:]
			m = re.match('[ \t]*(\[ *)(?P<ktime>[0-9\.]*)(\]) (?P<msg>.*)', line)
			if(not m):
				continue
			ktime = float(m.group('ktime'))
			if ktime > self.dmesgstart:
				op.write(line)
		fp.close()
		op.close()
	def listFromFile(self, file):
		list = []
		fp = open(file)
		for i in fp.read().split('\n'):
			i = i.strip()
			if i and i[0] != '#':
				list.append(i)
		fp.close()
		return list
	def addFtraceFilterFunctions(self, file):
		for i in self.listFromFile(file):
			if len(i) < 2:
				continue
			self.tracefuncs[i] = dict()
	def getFtraceFilterFunctions(self, current):
		self.rootCheck(True)
		if not current:
			call('cat '+self.tpath+'available_filter_functions', shell=True)
			return
		master = self.listFromFile(self.tpath+'available_filter_functions')
		for i in self.tracefuncs:
			if 'func' in self.tracefuncs[i]:
				i = self.tracefuncs[i]['func']
			if i in master:
				print i
			else:
				print self.colorText(i)
	def setFtraceFilterFunctions(self, list):
		master = self.listFromFile(self.tpath+'available_filter_functions')
		flist = ''
		for i in list:
			if i not in master:
				continue
			if ' [' in i:
				flist += i.split(' ')[0]+'\n'
			else:
				flist += i+'\n'
		fp = open(self.tpath+'set_graph_function', 'w')
		fp.write(flist)
		fp.close()
	def basicKprobe(self, name):
		self.kprobes[name] = {'name': name,'func': name,'args': dict(),'format': name}
	def defaultKprobe(self, name, kdata):
		k = kdata
		for field in ['name', 'format', 'func']:
			if field not in k:
				k[field] = name
		if self.archargs in k:
			k['args'] = k[self.archargs]
		else:
			k['args'] = dict()
			k['format'] = name
		self.kprobes[name] = k
	def kprobeColor(self, name):
		if name not in self.kprobes or 'color' not in self.kprobes[name]:
			return ''
		return self.kprobes[name]['color']
	def kprobeDisplayName(self, name, dataraw):
		if name not in self.kprobes:
			self.basicKprobe(name)
		data = ''
		quote=0
		# first remvoe any spaces inside quotes, and the quotes
		for c in dataraw:
			if c == '"':
				quote = (quote + 1) % 2
			if quote and c == ' ':
				data += '_'
			elif c != '"':
				data += c
		fmt, args = self.kprobes[name]['format'], self.kprobes[name]['args']
		arglist = dict()
		# now process the args
		for arg in sorted(args):
			arglist[arg] = ''
			m = re.match('.* '+arg+'=(?P<arg>.*) ', data);
			if m:
				arglist[arg] = m.group('arg')
			else:
				m = re.match('.* '+arg+'=(?P<arg>.*)', data);
				if m:
					arglist[arg] = m.group('arg')
		out = fmt.format(**arglist)
		out = out.replace(' ', '_').replace('"', '')
		return out
	def kprobeText(self, kname, kprobe):
		name = fmt = func = kname
		args = dict()
		if 'name' in kprobe:
			name = kprobe['name']
		if 'format' in kprobe:
			fmt = kprobe['format']
		if 'func' in kprobe:
			func = kprobe['func']
		if self.archargs in kprobe:
			args = kprobe[self.archargs]
		if 'args' in kprobe:
			args = kprobe['args']
		if re.findall('{(?P<n>[a-z,A-Z,0-9]*)}', func):
			doError('Kprobe "%s" has format info in the function name "%s"' % (name, func))
		for arg in re.findall('{(?P<n>[a-z,A-Z,0-9]*)}', fmt):
			if arg not in args:
				doError('Kprobe "%s" is missing argument "%s"' % (name, arg))
		val = 'p:%s_cal %s' % (name, func)
		for i in sorted(args):
			val += ' %s=%s' % (i, args[i])
		val += '\nr:%s_ret %s $retval\n' % (name, func)
		return val
	def addKprobes(self, output=False):
		if len(self.kprobes) < 1:
			return
		if output:
			print('    kprobe functions in this kernel:')
		# first test each kprobe
		rejects = []
		# sort kprobes: trace, ub-dev, custom, dev
		kpl = [[], [], [], []]
		linesout = len(self.kprobes)
		for name in sorted(self.kprobes):
			res = self.colorText('YES', 32)
			if not self.testKprobe(name, self.kprobes[name]):
				res = self.colorText('NO')
				rejects.append(name)
			else:
				if name in self.tracefuncs:
					kpl[0].append(name)
				elif name in self.dev_tracefuncs:
					if 'ub' in self.dev_tracefuncs[name]:
						kpl[1].append(name)
					else:
						kpl[3].append(name)
				else:
					kpl[2].append(name)
			if output:
				print('         %s: %s' % (name, res))
		kplist = kpl[0] + kpl[1] + kpl[2] + kpl[3]
		# remove all failed ones from the list
		for name in rejects:
			self.kprobes.pop(name)
		# set the kprobes all at once
		self.fsetVal('', 'kprobe_events')
		kprobeevents = ''
		for kp in kplist:
			kprobeevents += self.kprobeText(kp, self.kprobes[kp])
		self.fsetVal(kprobeevents, 'kprobe_events')
		if output:
			check = self.fgetVal('kprobe_events')
			linesack = (len(check.split('\n')) - 1) / 2
			print('    kprobe functions enabled: %d/%d' % (linesack, linesout))
		self.fsetVal('1', 'events/kprobes/enable')
	def testKprobe(self, kname, kprobe):
		self.fsetVal('0', 'events/kprobes/enable')
		kprobeevents = self.kprobeText(kname, kprobe)
		if not kprobeevents:
			return False
		try:
			self.fsetVal(kprobeevents, 'kprobe_events')
			check = self.fgetVal('kprobe_events')
		except:
			return False
		linesout = len(kprobeevents.split('\n'))
		linesack = len(check.split('\n'))
		if linesack < linesout:
			return False
		return True
	def setVal(self, val, file, mode='w'):
		if not os.path.exists(file):
			return False
		try:
			fp = open(file, mode, 0)
			fp.write(val)
			fp.flush()
			fp.close()
		except:
			return False
		return True
	def fsetVal(self, val, path, mode='w'):
		return self.setVal(val, self.tpath+path, mode)
	def getVal(self, file):
		res = ''
		if not os.path.exists(file):
			return res
		try:
			fp = open(file, 'r')
			res = fp.read()
			fp.close()
		except:
			pass
		return res
	def fgetVal(self, path):
		return self.getVal(self.tpath+path)
	def cleanupFtrace(self):
		if(self.usecallgraph or self.usetraceevents or self.usedevsrc):
			self.fsetVal('0', 'events/kprobes/enable')
			self.fsetVal('', 'kprobe_events')
			self.fsetVal('1024', 'buffer_size_kb')
	def setupAllKprobes(self):
		for name in self.tracefuncs:
			self.defaultKprobe(name, self.tracefuncs[name])
		for name in self.dev_tracefuncs:
			self.defaultKprobe(name, self.dev_tracefuncs[name])
	def isCallgraphFunc(self, name):
		if len(self.tracefuncs) < 1 and self.suspendmode == 'command':
			return True
		for i in self.tracefuncs:
			if 'func' in self.tracefuncs[i]:
				f = self.tracefuncs[i]['func']
			else:
				f = i
			if name == f:
				return True
		return False
	def initFtrace(self):
		self.printSystemInfo(False)
		print('INITIALIZING FTRACE...')
		# turn trace off
		self.fsetVal('0', 'tracing_on')
		self.cleanupFtrace()
		# set the trace clock to global
		self.fsetVal('global', 'trace_clock')
		self.fsetVal('nop', 'current_tracer')
		# set trace buffer to an appropriate value
		cpus = max(1, self.cpucount)
		if self.bufsize > 0:
			tgtsize = self.bufsize
		elif self.usecallgraph or self.usedevsrc:
			tgtsize = min(self.memfree, 3*1024*1024)
		else:
			tgtsize = 65536
		while not self.fsetVal('%d' % (tgtsize / cpus), 'buffer_size_kb'):
			# if the size failed to set, lower it and keep trying
			tgtsize -= 65536
			if tgtsize < 65536:
				tgtsize = int(self.fgetVal('buffer_size_kb')) * cpus
				break
		print 'Setting trace buffers to %d kB (%d kB per cpu)' % (tgtsize, tgtsize/cpus)
		# initialize the callgraph trace
		if(self.usecallgraph):
			# set trace type
			self.fsetVal('function_graph', 'current_tracer')
			self.fsetVal('', 'set_ftrace_filter')
			# set trace format options
			self.fsetVal('print-parent', 'trace_options')
			self.fsetVal('funcgraph-abstime', 'trace_options')
			self.fsetVal('funcgraph-cpu', 'trace_options')
			self.fsetVal('funcgraph-duration', 'trace_options')
			self.fsetVal('funcgraph-proc', 'trace_options')
			self.fsetVal('funcgraph-tail', 'trace_options')
			self.fsetVal('nofuncgraph-overhead', 'trace_options')
			self.fsetVal('context-info', 'trace_options')
			self.fsetVal('graph-time', 'trace_options')
			self.fsetVal('%d' % self.max_graph_depth, 'max_graph_depth')
			cf = ['dpm_run_callback']
			if(self.usetraceevents):
				cf += ['dpm_prepare', 'dpm_complete']
			for fn in self.tracefuncs:
				if 'func' in self.tracefuncs[fn]:
					cf.append(self.tracefuncs[fn]['func'])
				else:
					cf.append(fn)
			self.setFtraceFilterFunctions(cf)
		# initialize the kprobe trace
		elif self.usekprobes:
			for name in self.tracefuncs:
				self.defaultKprobe(name, self.tracefuncs[name])
			if self.usedevsrc:
				for name in self.dev_tracefuncs:
					self.defaultKprobe(name, self.dev_tracefuncs[name])
			print('INITIALIZING KPROBES...')
			self.addKprobes(self.verbose)
		if(self.usetraceevents):
			# turn trace events on
			events = iter(self.traceevents)
			for e in events:
				self.fsetVal('1', 'events/power/'+e+'/enable')
		# clear the trace buffer
		self.fsetVal('', 'trace')
	def verifyFtrace(self):
		# files needed for any trace data
		files = ['buffer_size_kb', 'current_tracer', 'trace', 'trace_clock',
				 'trace_marker', 'trace_options', 'tracing_on']
		# files needed for callgraph trace data
		tp = self.tpath
		if(self.usecallgraph):
			files += [
				'available_filter_functions',
				'set_ftrace_filter',
				'set_graph_function'
			]
		for f in files:
			if(os.path.exists(tp+f) == False):
				return False
		return True
	def verifyKprobes(self):
		# files needed for kprobes to work
		files = ['kprobe_events', 'events']
		tp = self.tpath
		for f in files:
			if(os.path.exists(tp+f) == False):
				return False
		return True
	def colorText(self, str, color=31):
		if not self.ansi:
			return str
		return '\x1B[%d;40m%s\x1B[m' % (color, str)
	def writeDatafileHeader(self, filename, fwdata, bdata):
		fp = self.openlog(filename, 'w')
		fp.write('%s\n%s\n# command | %s\n' % (self.teststamp, self.sysstamp, self.cmdline))
		if len(fwdata) > 0:
			for fw in fwdata:
				if(fw):
					fp.write('# fwsuspend %u fwresume %u\n' % (fw[0], fw[1]))
		if len(bdata) > 0:
			for b in bdata:
				(a1, c1), (a2, c2) = b
				fp.write('# battery %s %d %s %d\n' % (a1, c1, a2, c2))
		return fp
	def sudouser(self, dir):
		if os.path.exists(dir) and os.getuid() == 0 and \
			'SUDO_USER' in os.environ:
			cmd = 'chown -R {0}:{0} {1} > /dev/null 2>&1'
			call(cmd.format(os.environ['SUDO_USER'], dir), shell=True)
	def outputResult(self, testdata, num=0):
		if not self.result:
			return
		n = ''
		if num > 0:
			n = '%d' % num
		fp = open(self.result, 'a')
		if 'error' in testdata:
			fp.write('result%s: fail\n' % n)
			fp.write('error%s: %s\n' % (n, testdata['error']))
		else:
			fp.write('result%s: pass\n' % n)
		for v in ['suspend', 'resume', 'boot', 'lastinit']:
			if v in testdata:
				fp.write('%s%s: %.3f\n' % (v, n, testdata[v]))
		for v in ['fwsuspend', 'fwresume']:
			if v in testdata:
				fp.write('%s%s: %.3f\n' % (v, n, testdata[v] / 1000000.0))
		if 'bugurl' in testdata:
			fp.write('url%s: %s\n' % (n, testdata['bugurl']))
		fp.close()
		self.sudouser(self.result)
	def configFile(self, file):
		dir = os.path.dirname(os.path.realpath(__file__))
		if os.path.exists(file):
			return file
		elif os.path.exists(dir+'/'+file):
			return dir+'/'+file
		elif os.path.exists(dir+'/config/'+file):
			return dir+'/config/'+file
		return ''
	def openlog(self, filename, mode):
		isgz = self.gzip
		if mode == 'r':
			try:
				with gzip.open(filename, mode+'b') as fp:
					test = fp.read(64)
				isgz = True
			except:
				isgz = False
		if isgz:
			return gzip.open(filename, mode+'b')
		return open(filename, mode)

sysvals = SystemValues()
switchvalues = ['enable', 'disable', 'on', 'off', 'true', 'false', '1', '0']
switchoff = ['disable', 'off', 'false', '0']
suspendmodename = {
	'freeze': 'Freeze (S0)',
	'standby': 'Standby (S1)',
	'mem': 'Suspend (S3)',
	'disk': 'Hibernate (S4)'
}

# Class: DevProps
# Description:
#	 Simple class which holds property values collected
#	 for all the devices used in the timeline.
class DevProps:
	syspath = ''
	altname = ''
	async = True
	xtraclass = ''
	xtrainfo = ''
	def out(self, dev):
		return '%s,%s,%d;' % (dev, self.altname, self.async)
	def debug(self, dev):
		print '%s:\n\taltname = %s\n\t  async = %s' % (dev, self.altname, self.async)
	def altName(self, dev):
		if not self.altname or self.altname == dev:
			return dev
		return '%s [%s]' % (self.altname, dev)
	def xtraClass(self):
		if self.xtraclass:
			return ' '+self.xtraclass
		if not self.async:
			return ' sync'
		return ''
	def xtraInfo(self):
		if self.xtraclass:
			return ' '+self.xtraclass
		if self.async:
			return ' async_device'
		return ' sync_device'

# Class: DeviceNode
# Description:
#	 A container used to create a device hierachy, with a single root node
#	 and a tree of child nodes. Used by Data.deviceTopology()
class DeviceNode:
	name = ''
	children = 0
	depth = 0
	def __init__(self, nodename, nodedepth):
		self.name = nodename
		self.children = []
		self.depth = nodedepth

# Class: Data
# Description:
#	 The primary container for suspend/resume test data. There is one for
#	 each test run. The data is organized into a cronological hierarchy:
#	 Data.dmesg {
#		phases {
#			10 sequential, non-overlapping phases of S/R
#			contents: times for phase start/end, order/color data for html
#			devlist {
#				device callback or action list for this phase
#				device {
#					a single device callback or generic action
#					contents: start/stop times, pid/cpu/driver info
#						parents/children, html id for timeline/callgraph
#						optionally includes an ftrace callgraph
#						optionally includes dev/ps data
#				}
#			}
#		}
#	}
#
class Data:
	dmesg = {}  # root data structure
	start = 0.0 # test start
	end = 0.0   # test end
	tSuspended = 0.0 # low-level suspend start
	tResumed = 0.0   # low-level resume start
	tKernSus = 0.0   # kernel level suspend start
	tKernRes = 0.0   # kernel level resume end
	tLow = []        # time spent in low-level suspends (standby/freeze)
	fwValid = False  # is firmware data available
	fwSuspend = 0    # time spent in firmware suspend
	fwResume = 0     # time spent in firmware resume
	dmesgtext = []   # dmesg text file in memory
	pstl = 0         # process timeline
	testnumber = 0
	idstr = ''
	html_device_id = 0
	stamp = 0
	outfile = ''
	devpids = []
	kerror = False
	battery = 0
	phasedef = {
		'suspend_prepare': {'order': 0, 'color': '#CCFFCC'},
		        'suspend': {'order': 1, 'color': '#88FF88'},
		   'suspend_late': {'order': 2, 'color': '#00AA00'},
		  'suspend_noirq': {'order': 3, 'color': '#008888'},
		'suspend_machine': {'order': 4, 'color': '#0000FF'},
		 'resume_machine': {'order': 5, 'color': '#FF0000'},
		   'resume_noirq': {'order': 6, 'color': '#FF9900'},
		   'resume_early': {'order': 7, 'color': '#FFCC00'},
		         'resume': {'order': 8, 'color': '#FFFF88'},
		'resume_complete': {'order': 9, 'color': '#FFFFCC'},
	}
	currphase = ''
	def __init__(self, num):
		idchar = 'abcdefghij'
		self.pstl = dict()
		self.testnumber = num
		self.idstr = idchar[num]
		self.dmesgtext = []
		self.dmesg = dict()
		self.errorinfo = {'suspend':[],'resume':[]}
		self.tLow = []
		self.devpids = []
	def sortedPhases(self):
		return sorted(self.dmesg, key=lambda k:self.dmesg[k]['order'])
	def initDevicegroups(self):
		# called when phases are all finished being added
		for phase in self.dmesg.keys():
			if '*' in phase:
				p = phase.split('*')
				pnew = '%s%d' % (p[0], len(p))
				self.dmesg[pnew] = self.dmesg.pop(phase)
		self.devicegroups = []
		for phase in self.sortedPhases():
			self.devicegroups.append([phase])
	def nextPhase(self, phase, offset):
		order = self.dmesg[phase]['order'] + offset
		for p in self.dmesg:
			if self.dmesg[p]['order'] == order:
				return p
		return ''
	def lastPhase(self):
		plist = self.sortedPhases()
		if len(plist) < 1:
			return ''
		return plist[-1]
	def extractErrorInfo(self):
		elist = {
			'HWERROR' : '.*\[ *Hardware Error *\].*',
			'FWBUG'   : '.*\[ *Firmware Bug *\].*',
			'BUG'     : '.*BUG.*',
			'ERROR'   : '.*ERROR.*',
			'WARNING' : '.*WARNING.*',
			'IRQ'     : '.*genirq: .*',
			'TASKFAIL': '.*Freezing of tasks failed.*',
		}
		lf = sysvals.openlog(sysvals.dmesgfile, 'r')
		i = 0
		list = []
		for line in lf:
			i += 1
			m = re.match('[ \t]*(\[ *)(?P<ktime>[0-9\.]*)(\]) (?P<msg>.*)', line)
			if not m:
				continue
			t = float(m.group('ktime'))
			if t < self.start or t > self.end:
				continue
			dir = 'suspend' if t < self.tSuspended else 'resume'
			msg = m.group('msg')
			for err in elist:
				if re.match(elist[err], msg):
					list.append((err, dir, t, i, i))
					self.kerror = True
					break
		for e in list:
			type, dir, t, idx1, idx2 = e
			sysvals.vprint('kernel %s found in %s at %f' % (type, dir, t))
			self.errorinfo[dir].append((type, t, idx1, idx2))
		if self.kerror:
			sysvals.dmesglog = True
		lf.close()
	def setStart(self, time):
		self.start = time
	def setEnd(self, time):
		self.end = time
	def isTraceEventOutsideDeviceCalls(self, pid, time):
		for phase in self.sortedPhases():
			list = self.dmesg[phase]['list']
			for dev in list:
				d = list[dev]
				if(d['pid'] == pid and time >= d['start'] and
					time < d['end']):
					return False
		return True
	def sourcePhase(self, start):
		for phase in self.sortedPhases():
			pend = self.dmesg[phase]['end']
			if start <= pend:
				return phase
		return 'resume_complete'
	def sourceDevice(self, phaselist, start, end, pid, type):
		tgtdev = ''
		for phase in phaselist:
			list = self.dmesg[phase]['list']
			for devname in list:
				dev = list[devname]
				# pid must match
				if dev['pid'] != pid:
					continue
				devS = dev['start']
				devE = dev['end']
				if type == 'device':
					# device target event is entirely inside the source boundary
					if(start < devS or start >= devE or end <= devS or end > devE):
						continue
				elif type == 'thread':
					# thread target event will expand the source boundary
					if start < devS:
						dev['start'] = start
					if end > devE:
						dev['end'] = end
				tgtdev = dev
				break
		return tgtdev
	def addDeviceFunctionCall(self, displayname, kprobename, proc, pid, start, end, cdata, rdata):
		# try to place the call in a device
		phases = self.sortedPhases()
		tgtdev = self.sourceDevice(phases, start, end, pid, 'device')
		# calls with device pids that occur outside device bounds are dropped
		# TODO: include these somehow
		if not tgtdev and pid in self.devpids:
			return False
		# try to place the call in a thread
		if not tgtdev:
			tgtdev = self.sourceDevice(phases, start, end, pid, 'thread')
		# create new thread blocks, expand as new calls are found
		if not tgtdev:
			if proc == '<...>':
				threadname = 'kthread-%d' % (pid)
			else:
				threadname = '%s-%d' % (proc, pid)
			tgtphase = self.sourcePhase(start)
			self.newAction(tgtphase, threadname, pid, '', start, end, '', ' kth', '')
			return self.addDeviceFunctionCall(displayname, kprobename, proc, pid, start, end, cdata, rdata)
		# this should not happen
		if not tgtdev:
			sysvals.vprint('[%f - %f] %s-%d %s %s %s' % \
				(start, end, proc, pid, kprobename, cdata, rdata))
			return False
		# place the call data inside the src element of the tgtdev
		if('src' not in tgtdev):
			tgtdev['src'] = []
		dtf = sysvals.dev_tracefuncs
		ubiquitous = False
		if kprobename in dtf and 'ub' in dtf[kprobename]:
			ubiquitous = True
		title = cdata+' '+rdata
		mstr = '\(.*\) *(?P<args>.*) *\((?P<caller>.*)\+.* arg1=(?P<ret>.*)'
		m = re.match(mstr, title)
		if m:
			c = m.group('caller')
			a = m.group('args').strip()
			r = m.group('ret')
			if len(r) > 6:
				r = ''
			else:
				r = 'ret=%s ' % r
			if ubiquitous and c in dtf and 'ub' in dtf[c]:
				return False
		color = sysvals.kprobeColor(kprobename)
		e = DevFunction(displayname, a, c, r, start, end, ubiquitous, proc, pid, color)
		tgtdev['src'].append(e)
		return True
	def overflowDevices(self):
		# get a list of devices that extend beyond the end of this test run
		devlist = []
		for phase in self.sortedPhases():
			list = self.dmesg[phase]['list']
			for devname in list:
				dev = list[devname]
				if dev['end'] > self.end:
					devlist.append(dev)
		return devlist
	def mergeOverlapDevices(self, devlist):
		# merge any devices that overlap devlist
		for dev in devlist:
			devname = dev['name']
			for phase in self.sortedPhases():
				list = self.dmesg[phase]['list']
				if devname not in list:
					continue
				tdev = list[devname]
				o = min(dev['end'], tdev['end']) - max(dev['start'], tdev['start'])
				if o <= 0:
					continue
				dev['end'] = tdev['end']
				if 'src' not in dev or 'src' not in tdev:
					continue
				dev['src'] += tdev['src']
				del list[devname]
	def usurpTouchingThread(self, name, dev):
		# the caller test has priority of this thread, give it to him
		for phase in self.sortedPhases():
			list = self.dmesg[phase]['list']
			if name in list:
				tdev = list[name]
				if tdev['start'] - dev['end'] < 0.1:
					dev['end'] = tdev['end']
					if 'src' not in dev:
						dev['src'] = []
					if 'src' in tdev:
						dev['src'] += tdev['src']
					del list[name]
				break
	def stitchTouchingThreads(self, testlist):
		# merge any threads between tests that touch
		for phase in self.sortedPhases():
			list = self.dmesg[phase]['list']
			for devname in list:
				dev = list[devname]
				if 'htmlclass' not in dev or 'kth' not in dev['htmlclass']:
					continue
				for data in testlist:
					data.usurpTouchingThread(devname, dev)
	def optimizeDevSrc(self):
		# merge any src call loops to reduce timeline size
		for phase in self.sortedPhases():
			list = self.dmesg[phase]['list']
			for dev in list:
				if 'src' not in list[dev]:
					continue
				src = list[dev]['src']
				p = 0
				for e in sorted(src, key=lambda event: event.time):
					if not p or not e.repeat(p):
						p = e
						continue
					# e is another iteration of p, move it into p
					p.end = e.end
					p.length = p.end - p.time
					p.count += 1
					src.remove(e)
	def trimTimeVal(self, t, t0, dT, left):
		if left:
			if(t > t0):
				if(t - dT < t0):
					return t0
				return t - dT
			else:
				return t
		else:
			if(t < t0 + dT):
				if(t > t0):
					return t0 + dT
				return t + dT
			else:
				return t
	def trimTime(self, t0, dT, left):
		self.tSuspended = self.trimTimeVal(self.tSuspended, t0, dT, left)
		self.tResumed = self.trimTimeVal(self.tResumed, t0, dT, left)
		self.start = self.trimTimeVal(self.start, t0, dT, left)
		self.tKernSus = self.trimTimeVal(self.tKernSus, t0, dT, left)
		self.tKernRes = self.trimTimeVal(self.tKernRes, t0, dT, left)
		self.end = self.trimTimeVal(self.end, t0, dT, left)
		for phase in self.sortedPhases():
			p = self.dmesg[phase]
			p['start'] = self.trimTimeVal(p['start'], t0, dT, left)
			p['end'] = self.trimTimeVal(p['end'], t0, dT, left)
			list = p['list']
			for name in list:
				d = list[name]
				d['start'] = self.trimTimeVal(d['start'], t0, dT, left)
				d['end'] = self.trimTimeVal(d['end'], t0, dT, left)
				d['length'] = d['end'] - d['start']
				if('ftrace' in d):
					cg = d['ftrace']
					cg.start = self.trimTimeVal(cg.start, t0, dT, left)
					cg.end = self.trimTimeVal(cg.end, t0, dT, left)
					for line in cg.list:
						line.time = self.trimTimeVal(line.time, t0, dT, left)
				if('src' in d):
					for e in d['src']:
						e.time = self.trimTimeVal(e.time, t0, dT, left)
		for dir in ['suspend', 'resume']:
			list = []
			for e in self.errorinfo[dir]:
				type, tm, idx1, idx2 = e
				tm = self.trimTimeVal(tm, t0, dT, left)
				list.append((type, tm, idx1, idx2))
			self.errorinfo[dir] = list
	def trimFreezeTime(self, tZero):
		# trim out any standby or freeze clock time
		lp = ''
		for phase in self.sortedPhases():
			if 'resume_machine' in phase and 'suspend_machine' in lp:
				tS, tR = self.dmesg[lp]['end'], self.dmesg[phase]['start']
				tL = tR - tS
				if tL > 0:
					print '%s: %f - %f (%f)' % (phase, tS, tR, tL*1000)
					left = True if tR > tZero else False
					self.trimTime(tS, tL, left)
					self.tLow.append('%.0f'%(tL*1000))
			lp = phase
	def worstOffenders(self, devprops=False):
		out = []
		devlist = dict()
		for phase in self.dmesg:
			list = self.dmesg[phase]['list']
			for dev in list:
				devname = dev
				if devprops and dev in devprops and devprops[dev].altName(dev) != dev:
					devname = devprops[dev].altName(dev)
				if 'drv' in list[dev] and list[dev]['drv']:
					devname = '%s %s' % (list[dev]['drv'], devname)
				length = (list[dev]['end'] - list[dev]['start']) * 1000
				if devname not in devlist:
					devlist[devname] = length
				else:
					devlist[devname] += length
		for d in sorted(devlist, key=devlist.get, reverse=True):
			out.append('%s (%.0f ms)' % (d, devlist[d]))
			if len(out) >= 10:
				break
		return out
	def getTimeValues(self):
		if 'suspend_machine' in self.dmesg:
			sktime = (self.dmesg['suspend_machine']['end'] - \
				self.tKernSus) * 1000
		else:
			sktime = (self.tSuspended - self.tKernSus) * 1000
		if 'resume_machine' in self.dmesg:
			rktime = (self.tKernRes - \
				self.dmesg['resume_machine']['start']) * 1000
		else:
			rktime = (self.tKernRes - self.tResumed) * 1000
		return (sktime, rktime)
	def setPhase(self, phase, ktime, isbegin, order=-1):
		if(isbegin):
			# phase start over current phase
			if self.currphase:
				if 'resume_machine' not in self.currphase:
					print 'WARNING: phase %s failed to end' % self.currphase
				self.dmesg[self.currphase]['end'] = ktime
			phases = self.dmesg.keys()
			color = self.phasedef[phase]['color']
			count = len(phases) if order < 0 else order
			# create unique name for every new phase
			while phase in phases:
				phase += '*'
			self.dmesg[phase] = {'list': dict(), 'start': -1.0, 'end': -1.0,
				'row': 0, 'color': color, 'order': count}
			self.dmesg[phase]['start'] = ktime
			self.currphase = phase
		else:
			# phase end without a start
			if phase not in self.currphase:
				if self.currphase:
					print 'WARNING: %s ended instead of %s, ftrace corruption?' % (phase, self.currphase)
				else:
					print 'WARNING: %s ended without a start, ftrace corruption?' % phase
					return phase
			phase = self.currphase
			self.dmesg[phase]['end'] = ktime
			self.currphase = ''
		return phase
	def sortedDevices(self, phase):
		list = self.dmesg[phase]['list']
		slist = []
		tmp = dict()
		for devname in list:
			dev = list[devname]
			if dev['length'] == 0:
				continue
			tmp[dev['start']] = devname
		for t in sorted(tmp):
			slist.append(tmp[t])
		return slist
	def fixupInitcalls(self, phase):
		# if any calls never returned, clip them at system resume end
		phaselist = self.dmesg[phase]['list']
		for devname in phaselist:
			dev = phaselist[devname]
			if(dev['end'] < 0):
				for p in self.sortedPhases():
					if self.dmesg[p]['end'] > dev['start']:
						dev['end'] = self.dmesg[p]['end']
						break
				sysvals.vprint('%s (%s): callback didnt return' % (devname, phase))
	def deviceFilter(self, devicefilter):
		for phase in self.sortedPhases():
			list = self.dmesg[phase]['list']
			rmlist = []
			for name in list:
				keep = False
				for filter in devicefilter:
					if filter in name or \
						('drv' in list[name] and filter in list[name]['drv']):
						keep = True
				if not keep:
					rmlist.append(name)
			for name in rmlist:
				del list[name]
	def fixupInitcallsThatDidntReturn(self):
		# if any calls never returned, clip them at system resume end
		for phase in self.sortedPhases():
			self.fixupInitcalls(phase)
	def phaseOverlap(self, phases):
		rmgroups = []
		newgroup = []
		for group in self.devicegroups:
			for phase in phases:
				if phase not in group:
					continue
				for p in group:
					if p not in newgroup:
						newgroup.append(p)
				if group not in rmgroups:
					rmgroups.append(group)
		for group in rmgroups:
			self.devicegroups.remove(group)
		self.devicegroups.append(newgroup)
	def newActionGlobal(self, name, start, end, pid=-1, color=''):
		# which phase is this device callback or action in
		phases = self.sortedPhases()
		targetphase = 'none'
		htmlclass = ''
		overlap = 0.0
		myphases = []
		for phase in phases:
			pstart = self.dmesg[phase]['start']
			pend = self.dmesg[phase]['end']
			# see if the action overlaps this phase
			o = max(0, min(end, pend) - max(start, pstart))
			if o > 0:
				myphases.append(phase)
			# set the target phase to the one that overlaps most
			if o > overlap:
				if overlap > 0 and phase == 'post_resume':
					continue
				targetphase = phase
				overlap = o
		# if no target phase was found, pin it to the edge
		if targetphase == 'none':
			p0start = self.dmesg[phases[0]]['start']
			if start <= p0start:
				targetphase = phases[0]
			else:
				targetphase = phases[-1]
		if pid == -2:
			htmlclass = ' bg'
		elif pid == -3:
			htmlclass = ' ps'
		if len(myphases) > 1:
			htmlclass = ' bg'
			self.phaseOverlap(myphases)
		if targetphase in phases:
			newname = self.newAction(targetphase, name, pid, '', start, end, '', htmlclass, color)
			return (targetphase, newname)
		return False
	def newAction(self, phase, name, pid, parent, start, end, drv, htmlclass='', color=''):
		# new device callback for a specific phase
		self.html_device_id += 1
		devid = '%s%d' % (self.idstr, self.html_device_id)
		list = self.dmesg[phase]['list']
		length = -1.0
		if(start >= 0 and end >= 0):
			length = end - start
		if pid == -2:
			i = 2
			origname = name
			while(name in list):
				name = '%s[%d]' % (origname, i)
				i += 1
		list[name] = {'name': name, 'start': start, 'end': end, 'pid': pid,
			'par': parent, 'length': length, 'row': 0, 'id': devid, 'drv': drv }
		if htmlclass:
			list[name]['htmlclass'] = htmlclass
		if color:
			list[name]['color'] = color
		return name
	def deviceChildren(self, devname, phase):
		devlist = []
		list = self.dmesg[phase]['list']
		for child in list:
			if(list[child]['par'] == devname):
				devlist.append(child)
		return devlist
	def printDetails(self):
		sysvals.vprint('Timeline Details:')
		sysvals.vprint('          test start: %f' % self.start)
		sysvals.vprint('kernel suspend start: %f' % self.tKernSus)
		for phase in self.sortedPhases():
			dc = len(self.dmesg[phase]['list'])
			sysvals.vprint('    %16s: %f - %f (%d devices)' % (phase, \
				self.dmesg[phase]['start'], self.dmesg[phase]['end'], dc))
		sysvals.vprint('   kernel resume end: %f' % self.tKernRes)
		sysvals.vprint('            test end: %f' % self.end)
	def deviceChildrenAllPhases(self, devname):
		devlist = []
		for phase in self.sortedPhases():
			list = self.deviceChildren(devname, phase)
			for dev in list:
				if dev not in devlist:
					devlist.append(dev)
		return devlist
	def masterTopology(self, name, list, depth):
		node = DeviceNode(name, depth)
		for cname in list:
			# avoid recursions
			if name == cname:
				continue
			clist = self.deviceChildrenAllPhases(cname)
			cnode = self.masterTopology(cname, clist, depth+1)
			node.children.append(cnode)
		return node
	def printTopology(self, node):
		html = ''
		if node.name:
			info = ''
			drv = ''
			for phase in self.sortedPhases():
				list = self.dmesg[phase]['list']
				if node.name in list:
					s = list[node.name]['start']
					e = list[node.name]['end']
					if list[node.name]['drv']:
						drv = ' {'+list[node.name]['drv']+'}'
					info += ('<li>%s: %.3fms</li>' % (phase, (e-s)*1000))
			html += '<li><b>'+node.name+drv+'</b>'
			if info:
				html += '<ul>'+info+'</ul>'
			html += '</li>'
		if len(node.children) > 0:
			html += '<ul>'
			for cnode in node.children:
				html += self.printTopology(cnode)
			html += '</ul>'
		return html
	def rootDeviceList(self):
		# list of devices graphed
		real = []
		for phase in self.dmesg:
			list = self.dmesg[phase]['list']
			for dev in list:
				if list[dev]['pid'] >= 0 and dev not in real:
					real.append(dev)
		# list of top-most root devices
		rootlist = []
		for phase in self.dmesg:
			list = self.dmesg[phase]['list']
			for dev in list:
				pdev = list[dev]['par']
				pid = list[dev]['pid']
				if(pid < 0 or re.match('[0-9]*-[0-9]*\.[0-9]*[\.0-9]*\:[\.0-9]*$', pdev)):
					continue
				if pdev and pdev not in real and pdev not in rootlist:
					rootlist.append(pdev)
		return rootlist
	def deviceTopology(self):
		rootlist = self.rootDeviceList()
		master = self.masterTopology('', rootlist, 0)
		return self.printTopology(master)
	def selectTimelineDevices(self, widfmt, tTotal, mindevlen):
		# only select devices that will actually show up in html
		self.tdevlist = dict()
		for phase in self.dmesg:
			devlist = []
			list = self.dmesg[phase]['list']
			for dev in list:
				length = (list[dev]['end'] - list[dev]['start']) * 1000
				width = widfmt % (((list[dev]['end']-list[dev]['start'])*100)/tTotal)
				if width != '0.000000' and length >= mindevlen:
					devlist.append(dev)
			self.tdevlist[phase] = devlist
	def addHorizontalDivider(self, devname, devend):
		phase = 'suspend_prepare'
		self.newAction(phase, devname, -2, '', \
			self.start, devend, '', ' sec', '')
		if phase not in self.tdevlist:
			self.tdevlist[phase] = []
		self.tdevlist[phase].append(devname)
		d = DevItem(0, phase, self.dmesg[phase]['list'][devname])
		return d
	def addProcessUsageEvent(self, name, times):
		# get the start and end times for this process
		maxC = 0
		tlast = 0
		start = -1
		end = -1
		for t in sorted(times):
			if tlast == 0:
				tlast = t
				continue
			if name in self.pstl[t]:
				if start == -1 or tlast < start:
					start = tlast
				if end == -1 or t > end:
					end = t
			tlast = t
		if start == -1 or end == -1:
			return 0
		# add a new action for this process and get the object
		out = self.newActionGlobal(name, start, end, -3)
		if not out:
			return 0
		phase, devname = out
		dev = self.dmesg[phase]['list'][devname]
		# get the cpu exec data
		tlast = 0
		clast = 0
		cpuexec = dict()
		for t in sorted(times):
			if tlast == 0 or t <= start or t > end:
				tlast = t
				continue
			list = self.pstl[t]
			c = 0
			if name in list:
				c = list[name]
			if c > maxC:
				maxC = c
			if c != clast:
				key = (tlast, t)
				cpuexec[key] = c
				tlast = t
				clast = c
		dev['cpuexec'] = cpuexec
		return maxC
	def createProcessUsageEvents(self):
		# get an array of process names
		proclist = []
		for t in self.pstl:
			pslist = self.pstl[t]
			for ps in pslist:
				if ps not in proclist:
					proclist.append(ps)
		# get a list of data points for suspend and resume
		tsus = []
		tres = []
		for t in sorted(self.pstl):
			if t < self.tSuspended:
				tsus.append(t)
			else:
				tres.append(t)
		# process the events for suspend and resume
		if len(proclist) > 0:
			sysvals.vprint('Process Execution:')
		for ps in proclist:
			c = self.addProcessUsageEvent(ps, tsus)
			if c > 0:
				sysvals.vprint('%25s (sus): %d' % (ps, c))
			c = self.addProcessUsageEvent(ps, tres)
			if c > 0:
				sysvals.vprint('%25s (res): %d' % (ps, c))
	def debugPrint(self):
		for p in self.sortedPhases():
			list = self.dmesg[p]['list']
			for devname in list:
				dev = list[devname]
				if 'ftrace' in dev:
					dev['ftrace'].debugPrint(' [%s]' % devname)

# Class: DevFunction
# Description:
#	 A container for kprobe function data we want in the dev timeline
class DevFunction:
	row = 0
	count = 1
	def __init__(self, name, args, caller, ret, start, end, u, proc, pid, color):
		self.name = name
		self.args = args
		self.caller = caller
		self.ret = ret
		self.time = start
		self.length = end - start
		self.end = end
		self.ubiquitous = u
		self.proc = proc
		self.pid = pid
		self.color = color
	def title(self):
		cnt = ''
		if self.count > 1:
			cnt = '(x%d)' % self.count
		l = '%0.3fms' % (self.length * 1000)
		if self.ubiquitous:
			title = '%s(%s)%s <- %s, %s(%s)' % \
				(self.name, self.args, cnt, self.caller, self.ret, l)
		else:
			title = '%s(%s) %s%s(%s)' % (self.name, self.args, self.ret, cnt, l)
		return title.replace('"', '')
	def text(self):
		if self.count > 1:
			text = '%s(x%d)' % (self.name, self.count)
		else:
			text = self.name
		return text
	def repeat(self, tgt):
		# is the tgt call just a repeat of this call (e.g. are we in a loop)
		dt = self.time - tgt.end
		# only combine calls if -all- attributes are identical
		if tgt.caller == self.caller and \
			tgt.name == self.name and tgt.args == self.args and \
			tgt.proc == self.proc and tgt.pid == self.pid and \
			tgt.ret == self.ret and dt >= 0 and \
			dt <= sysvals.callloopmaxgap and \
			self.length < sysvals.callloopmaxlen:
			return True
		return False

# Class: FTraceLine
# Description:
#	 A container for a single line of ftrace data. There are six basic types:
#		 callgraph line:
#			  call: "  dpm_run_callback() {"
#			return: "  }"
#			  leaf: " dpm_run_callback();"
#		 trace event:
#			 tracing_mark_write: SUSPEND START or RESUME COMPLETE
#			 suspend_resume: phase or custom exec block data
#			 device_pm_callback: device callback info
class FTraceLine:
	time = 0.0
	length = 0.0
	fcall = False
	freturn = False
	fevent = False
	fkprobe = False
	depth = 0
	name = ''
	type = ''
	def __init__(self, t, m='', d=''):
		self.time = float(t)
		if not m and not d:
			return
		# is this a trace event
		if(d == 'traceevent' or re.match('^ *\/\* *(?P<msg>.*) \*\/ *$', m)):
			if(d == 'traceevent'):
				# nop format trace event
				msg = m
			else:
				# function_graph format trace event
				em = re.match('^ *\/\* *(?P<msg>.*) \*\/ *$', m)
				msg = em.group('msg')

			emm = re.match('^(?P<call>.*?): (?P<msg>.*)', msg)
			if(emm):
				self.name = emm.group('msg')
				self.type = emm.group('call')
			else:
				self.name = msg
			km = re.match('^(?P<n>.*)_cal$', self.type)
			if km:
				self.fcall = True
				self.fkprobe = True
				self.type = km.group('n')
				return
			km = re.match('^(?P<n>.*)_ret$', self.type)
			if km:
				self.freturn = True
				self.fkprobe = True
				self.type = km.group('n')
				return
			self.fevent = True
			return
		# convert the duration to seconds
		if(d):
			self.length = float(d)/1000000
		# the indentation determines the depth
		match = re.match('^(?P<d> *)(?P<o>.*)$', m)
		if(not match):
			return
		self.depth = self.getDepth(match.group('d'))
		m = match.group('o')
		# function return
		if(m[0] == '}'):
			self.freturn = True
			if(len(m) > 1):
				# includes comment with function name
				match = re.match('^} *\/\* *(?P<n>.*) *\*\/$', m)
				if(match):
					self.name = match.group('n').strip()
		# function call
		else:
			self.fcall = True
			# function call with children
			if(m[-1] == '{'):
				match = re.match('^(?P<n>.*) *\(.*', m)
				if(match):
					self.name = match.group('n').strip()
			# function call with no children (leaf)
			elif(m[-1] == ';'):
				self.freturn = True
				match = re.match('^(?P<n>.*) *\(.*', m)
				if(match):
					self.name = match.group('n').strip()
			# something else (possibly a trace marker)
			else:
				self.name = m
	def isCall(self):
		return self.fcall and not self.freturn
	def isReturn(self):
		return self.freturn and not self.fcall
	def isLeaf(self):
		return self.fcall and self.freturn
	def getDepth(self, str):
		return len(str)/2
	def debugPrint(self, info=''):
		if self.isLeaf():
			print(' -- %12.6f (depth=%02d): %s(); (%.3f us) %s' % (self.time, \
				self.depth, self.name, self.length*1000000, info))
		elif self.freturn:
			print(' -- %12.6f (depth=%02d): %s} (%.3f us) %s' % (self.time, \
				self.depth, self.name, self.length*1000000, info))
		else:
			print(' -- %12.6f (depth=%02d): %s() { (%.3f us) %s' % (self.time, \
				self.depth, self.name, self.length*1000000, info))
	def startMarker(self):
		# Is this the starting line of a suspend?
		if not self.fevent:
			return False
		if sysvals.usetracemarkers:
			if(self.name == 'SUSPEND START'):
				return True
			return False
		else:
			if(self.type == 'suspend_resume' and
				re.match('suspend_enter\[.*\] begin', self.name)):
				return True
			return False
	def endMarker(self):
		# Is this the ending line of a resume?
		if not self.fevent:
			return False
		if sysvals.usetracemarkers:
			if(self.name == 'RESUME COMPLETE'):
				return True
			return False
		else:
			if(self.type == 'suspend_resume' and
				re.match('thaw_processes\[.*\] end', self.name)):
				return True
			return False

# Class: FTraceCallGraph
# Description:
#	 A container for the ftrace callgraph of a single recursive function.
#	 This can be a dpm_run_callback, dpm_prepare, or dpm_complete callgraph
#	 Each instance is tied to a single device in a single phase, and is
#	 comprised of an ordered list of FTraceLine objects
class FTraceCallGraph:
	id = ''
	start = -1.0
	end = -1.0
	list = []
	invalid = False
	depth = 0
	pid = 0
	name = ''
	partial = False
	vfname = 'missing_function_name'
	ignore = False
	sv = 0
	def __init__(self, pid, sv):
		self.start = -1.0
		self.end = -1.0
		self.list = []
		self.depth = 0
		self.pid = pid
		self.sv = sv
	def addLine(self, line):
		# if this is already invalid, just leave
		if(self.invalid):
			if(line.depth == 0 and line.freturn):
				return 1
			return 0
		# invalidate on bad depth
		if(self.depth < 0):
			self.invalidate(line)
			return 0
		# ignore data til we return to the current depth
		if self.ignore:
			if line.depth > self.depth:
				return 0
			else:
				self.list[-1].freturn = True
				self.list[-1].length = line.time - self.list[-1].time
				self.ignore = False
				# if this is a return at self.depth, no more work is needed
				if line.depth == self.depth and line.isReturn():
					if line.depth == 0:
						self.end = line.time
						return 1
					return 0
		# compare current depth with this lines pre-call depth
		prelinedep = line.depth
		if line.isReturn():
			prelinedep += 1
		last = 0
		lasttime = line.time
		if len(self.list) > 0:
			last = self.list[-1]
			lasttime = last.time
			if last.isLeaf():
				lasttime += last.length
		# handle low misalignments by inserting returns
		mismatch = prelinedep - self.depth
		warning = self.sv.verbose and abs(mismatch) > 1
		info = []
		if mismatch < 0:
			idx = 0
			# add return calls to get the depth down
			while prelinedep < self.depth:
				self.depth -= 1
				if idx == 0 and last and last.isCall():
					# special case, turn last call into a leaf
					last.depth = self.depth
					last.freturn = True
					last.length = line.time - last.time
					if warning:
						info.append(('[make leaf]', last))
				else:
					vline = FTraceLine(lasttime)
					vline.depth = self.depth
					vline.name = self.vfname
					vline.freturn = True
					self.list.append(vline)
					if warning:
						if idx == 0:
							info.append(('', last))
						info.append(('[add return]', vline))
				idx += 1
			if warning:
				info.append(('', line))
		# handle high misalignments by inserting calls
		elif mismatch > 0:
			idx = 0
			if warning:
				info.append(('', last))
			# add calls to get the depth up
			while prelinedep > self.depth:
				if idx == 0 and line.isReturn():
					# special case, turn this return into a leaf
					line.fcall = True
					prelinedep -= 1
					if warning:
						info.append(('[make leaf]', line))
				else:
					vline = FTraceLine(lasttime)
					vline.depth = self.depth
					vline.name = self.vfname
					vline.fcall = True
					self.list.append(vline)
					self.depth += 1
					if not last:
						self.start = vline.time
					if warning:
						info.append(('[add call]', vline))
				idx += 1
			if warning and ('[make leaf]', line) not in info:
				info.append(('', line))
		if warning:
			print 'WARNING: ftrace data missing, corrections made:'
			for i in info:
				t, obj = i
				if obj:
					obj.debugPrint(t)
		# process the call and set the new depth
		skipadd = False
		md = self.sv.max_graph_depth
		if line.isCall():
			# ignore blacklisted/overdepth funcs
			if (md and self.depth >= md - 1) or (line.name in self.sv.cgblacklist):
				self.ignore = True
			else:
				self.depth += 1
		elif line.isReturn():
			self.depth -= 1
			# remove blacklisted/overdepth/empty funcs that slipped through
			if (last and last.isCall() and last.depth == line.depth) or \
				(md and last and last.depth >= md) or \
				(line.name in self.sv.cgblacklist):
				while len(self.list) > 0 and self.list[-1].depth > line.depth:
					self.list.pop(-1)
				if len(self.list) == 0:
					self.invalid = True
					return 1
				self.list[-1].freturn = True
				self.list[-1].length = line.time - self.list[-1].time
				self.list[-1].name = line.name
				skipadd = True
		if len(self.list) < 1:
			self.start = line.time
		# check for a mismatch that returned all the way to callgraph end
		res = 1
		if mismatch < 0 and self.list[-1].depth == 0 and self.list[-1].freturn:
			line = self.list[-1]
			skipadd = True
			res = -1
		if not skipadd:
			self.list.append(line)
		if(line.depth == 0 and line.freturn):
			if(self.start < 0):
				self.start = line.time
			self.end = line.time
			if line.fcall:
				self.end += line.length
			if self.list[0].name == self.vfname:
				self.invalid = True
			if res == -1:
				self.partial = True
			return res
		return 0
	def invalidate(self, line):
		if(len(self.list) > 0):
			first = self.list[0]
			self.list = []
			self.list.append(first)
		self.invalid = True
		id = 'task %s' % (self.pid)
		window = '(%f - %f)' % (self.start, line.time)
		if(self.depth < 0):
			print('Data misalignment for '+id+\
				' (buffer overflow), ignoring this callback')
		else:
			print('Too much data for '+id+\
				' '+window+', ignoring this callback')
	def slice(self, dev):
		minicg = FTraceCallGraph(dev['pid'], self.sv)
		minicg.name = self.name
		mydepth = -1
		good = False
		for l in self.list:
			if(l.time < dev['start'] or l.time > dev['end']):
				continue
			if mydepth < 0:
				if l.name == 'mutex_lock' and l.freturn:
					mydepth = l.depth
				continue
			elif l.depth == mydepth and l.name == 'mutex_unlock' and l.fcall:
				good = True
				break
			l.depth -= mydepth
			minicg.addLine(l)
		if not good or len(minicg.list) < 1:
			return 0
		return minicg
	def repair(self, enddepth):
		# bring the depth back to 0 with additional returns
		fixed = False
		last = self.list[-1]
		for i in reversed(range(enddepth)):
			t = FTraceLine(last.time)
			t.depth = i
			t.freturn = True
			fixed = self.addLine(t)
			if fixed != 0:
				self.end = last.time
				return True
		return False
	def postProcess(self):
		if len(self.list) > 0:
			self.name = self.list[0].name
		stack = dict()
		cnt = 0
		last = 0
		for l in self.list:
			# ftrace bug: reported duration is not reliable
			# check each leaf and clip it at max possible length
			if last and last.isLeaf():
				if last.length > l.time - last.time:
					last.length = l.time - last.time
			if l.isCall():
				stack[l.depth] = l
				cnt += 1
			elif l.isReturn():
				if(l.depth not in stack):
					if self.sv.verbose:
						print 'Post Process Error: Depth missing'
						l.debugPrint()
					return False
				# calculate call length from call/return lines
				cl = stack[l.depth]
				cl.length = l.time - cl.time
				if cl.name == self.vfname:
					cl.name = l.name
				stack.pop(l.depth)
				l.length = 0
				cnt -= 1
			last = l
		if(cnt == 0):
			# trace caught the whole call tree
			return True
		elif(cnt < 0):
			if self.sv.verbose:
				print 'Post Process Error: Depth is less than 0'
			return False
		# trace ended before call tree finished
		return self.repair(cnt)
	def deviceMatch(self, pid, data):
		found = ''
		# add the callgraph data to the device hierarchy
		borderphase = {
			'dpm_prepare': 'suspend_prepare',
			'dpm_complete': 'resume_complete'
		}
		if(self.name in borderphase):
			p = borderphase[self.name]
			list = data.dmesg[p]['list']
			for devname in list:
				dev = list[devname]
				if(pid == dev['pid'] and
					self.start <= dev['start'] and
					self.end >= dev['end']):
					cg = self.slice(dev)
					if cg:
						dev['ftrace'] = cg
					found = devname
			return found
		for p in data.sortedPhases():
			if(data.dmesg[p]['start'] <= self.start and
				self.start <= data.dmesg[p]['end']):
				list = data.dmesg[p]['list']
				for devname in list:
					dev = list[devname]
					if(pid == dev['pid'] and
						self.start <= dev['start'] and
						self.end >= dev['end']):
						dev['ftrace'] = self
						found = devname
						break
				break
		return found
	def newActionFromFunction(self, data):
		name = self.name
		if name in ['dpm_run_callback', 'dpm_prepare', 'dpm_complete']:
			return
		fs = self.start
		fe = self.end
		if fs < data.start or fe > data.end:
			return
		phase = ''
		for p in data.sortedPhases():
			if(data.dmesg[p]['start'] <= self.start and
				self.start < data.dmesg[p]['end']):
				phase = p
				break
		if not phase:
			return
		out = data.newActionGlobal(name, fs, fe, -2)
		if out:
			phase, myname = out
			data.dmesg[phase]['list'][myname]['ftrace'] = self
	def debugPrint(self, info=''):
		print('%s pid=%d [%f - %f] %.3f us') % \
			(self.name, self.pid, self.start, self.end,
			(self.end - self.start)*1000000)
		for l in self.list:
			if l.isLeaf():
				print('%f (%02d): %s(); (%.3f us)%s' % (l.time, \
					l.depth, l.name, l.length*1000000, info))
			elif l.freturn:
				print('%f (%02d): %s} (%.3f us)%s' % (l.time, \
					l.depth, l.name, l.length*1000000, info))
			else:
				print('%f (%02d): %s() { (%.3f us)%s' % (l.time, \
					l.depth, l.name, l.length*1000000, info))
		print(' ')

class DevItem:
	def __init__(self, test, phase, dev):
		self.test = test
		self.phase = phase
		self.dev = dev
	def isa(self, cls):
		if 'htmlclass' in self.dev and cls in self.dev['htmlclass']:
			return True
		return False

# Class: Timeline
# Description:
#	 A container for a device timeline which calculates
#	 all the html properties to display it correctly
class Timeline:
	html = ''
	height = 0	# total timeline height
	scaleH = 20	# timescale (top) row height
	rowH = 30	# device row height
	bodyH = 0	# body height
	rows = 0	# total timeline rows
	rowlines = dict()
	rowheight = dict()
	html_tblock = '<div id="block{0}" class="tblock" style="left:{1}%;width:{2}%;"><div class="tback" style="height:{3}px"></div>\n'
	html_device = '<div id="{0}" title="{1}" class="thread{7}" style="left:{2}%;top:{3}px;height:{4}px;width:{5}%;{8}">{6}</div>\n'
	html_phase = '<div class="phase" style="left:{0}%;width:{1}%;top:{2}px;height:{3}px;background:{4}">{5}</div>\n'
	html_phaselet = '<div id="{0}" class="phaselet" style="left:{1}%;width:{2}%;background:{3}"></div>\n'
	html_legend = '<div id="p{3}" class="square" style="left:{0}%;background:{1}">&nbsp;{2}</div>\n'
	def __init__(self, rowheight, scaleheight):
		self.rowH = rowheight
		self.scaleH = scaleheight
		self.html = ''
	def createHeader(self, sv, stamp, urlparams=''):
		if(not stamp['time']):
			return
		self.html += '<div class="version"><a href="https://01.org/suspendresume">%s v%s</a></div>' \
			% (sv.title, sv.version)
		if 'man' in stamp and 'plat' in stamp and 'cpu' in stamp and urlparams and \
			stamp['man'] and stamp['plat'] and stamp['cpu']:
			url = stamp['url'].replace('/rest', '/buglist')+\
				'?query_format=advanced&product=pm-graph&component='+stamp['app']+\
				'&cf_platform='+stamp['plat']+\
				'&cf_cpu='+stamp['cpu']+\
				'&cf_manufacturer='+stamp['man']+\
				'&cf_power_mode='+stamp['mode']+urlparams
			self.html += '<button class="btnfmt" onclick=\'window.open("'+url+'")\'>matches</button>'
		if sv.logmsg and sv.testlog:
			self.html += '<button id="showtest" class="logbtn btnfmt">log</button>'
		if sv.dmesglog:
			self.html += '<button id="showdmesg" class="logbtn btnfmt">dmesg</button>'
		if sv.ftracelog:
			self.html += '<button id="showftrace" class="logbtn btnfmt">ftrace</button>'
		headline_stamp = '<div class="stamp">{0} {1} {2} {3}</div>\n'
		self.html += headline_stamp.format(stamp['host'], stamp['kernel'],
			stamp['mode'], stamp['time'])
		if 'man' in stamp and 'plat' in stamp and 'cpu' in stamp and \
			stamp['man'] and stamp['plat'] and stamp['cpu']:
			headline_sysinfo = '<div class="stamp sysinfo">{0} {1} <i>with</i> {2}</div>\n'
			self.html += headline_sysinfo.format(stamp['man'], stamp['plat'], stamp['cpu'])

	# Function: getDeviceRows
	# Description:
	#    determine how may rows the device funcs will take
	# Arguments:
	#	 rawlist: the list of devices/actions for a single phase
	# Output:
	#	 The total number of rows needed to display this phase of the timeline
	def getDeviceRows(self, rawlist):
		# clear all rows and set them to undefined
		sortdict = dict()
		for item in rawlist:
			item.row = -1
			sortdict[item] = item.length
		sortlist = sorted(sortdict, key=sortdict.get, reverse=True)
		remaining = len(sortlist)
		rowdata = dict()
		row = 1
		# try to pack each row with as many ranges as possible
		while(remaining > 0):
			if(row not in rowdata):
				rowdata[row] = []
			for i in sortlist:
				if(i.row >= 0):
					continue
				s = i.time
				e = i.time + i.length
				valid = True
				for ritem in rowdata[row]:
					rs = ritem.time
					re = ritem.time + ritem.length
					if(not (((s <= rs) and (e <= rs)) or
						((s >= re) and (e >= re)))):
						valid = False
						break
				if(valid):
					rowdata[row].append(i)
					i.row = row
					remaining -= 1
			row += 1
		return row
	# Function: getPhaseRows
	# Description:
	#	 Organize the timeline entries into the smallest
	#	 number of rows possible, with no entry overlapping
	# Arguments:
	#	 devlist: the list of devices/actions in a group of contiguous phases
	# Output:
	#	 The total number of rows needed to display this phase of the timeline
	def getPhaseRows(self, devlist, row=0, sortby='length'):
		# clear all rows and set them to undefined
		remaining = len(devlist)
		rowdata = dict()
		sortdict = dict()
		myphases = []
		# initialize all device rows to -1 and calculate devrows
		for item in devlist:
			dev = item.dev
			tp = (item.test, item.phase)
			if tp not in myphases:
				myphases.append(tp)
			dev['row'] = -1
			if sortby == 'start':
				# sort by start 1st, then length 2nd
				sortdict[item] = (-1*float(dev['start']), float(dev['end']) - float(dev['start']))
			else:
				# sort by length 1st, then name 2nd
				sortdict[item] = (float(dev['end']) - float(dev['start']), item.dev['name'])
			if 'src' in dev:
				dev['devrows'] = self.getDeviceRows(dev['src'])
		# sort the devlist by length so that large items graph on top
		sortlist = sorted(sortdict, key=sortdict.get, reverse=True)
		orderedlist = []
		for item in sortlist:
			if item.dev['pid'] == -2:
				orderedlist.append(item)
		for item in sortlist:
			if item not in orderedlist:
				orderedlist.append(item)
		# try to pack each row with as many devices as possible
		while(remaining > 0):
			rowheight = 1
			if(row not in rowdata):
				rowdata[row] = []
			for item in orderedlist:
				dev = item.dev
				if(dev['row'] < 0):
					s = dev['start']
					e = dev['end']
					valid = True
					for ritem in rowdata[row]:
						rs = ritem.dev['start']
						re = ritem.dev['end']
						if(not (((s <= rs) and (e <= rs)) or
							((s >= re) and (e >= re)))):
							valid = False
							break
					if(valid):
						rowdata[row].append(item)
						dev['row'] = row
						remaining -= 1
						if 'devrows' in dev and dev['devrows'] > rowheight:
							rowheight = dev['devrows']
			for t, p in myphases:
				if t not in self.rowlines or t not in self.rowheight:
					self.rowlines[t] = dict()
					self.rowheight[t] = dict()
				if p not in self.rowlines[t] or p not in self.rowheight[t]:
					self.rowlines[t][p] = dict()
					self.rowheight[t][p] = dict()
				rh = self.rowH
				# section headers should use a different row height
				if len(rowdata[row]) == 1 and \
					'htmlclass' in rowdata[row][0].dev and \
					'sec' in rowdata[row][0].dev['htmlclass']:
					rh = 15
				self.rowlines[t][p][row] = rowheight
				self.rowheight[t][p][row] = rowheight * rh
			row += 1
		if(row > self.rows):
			self.rows = int(row)
		return row
	def phaseRowHeight(self, test, phase, row):
		return self.rowheight[test][phase][row]
	def phaseRowTop(self, test, phase, row):
		top = 0
		for i in sorted(self.rowheight[test][phase]):
			if i >= row:
				break
			top += self.rowheight[test][phase][i]
		return top
	def calcTotalRows(self):
		# Calculate the heights and offsets for the header and rows
		maxrows = 0
		standardphases = []
		for t in self.rowlines:
			for p in self.rowlines[t]:
				total = 0
				for i in sorted(self.rowlines[t][p]):
					total += self.rowlines[t][p][i]
				if total > maxrows:
					maxrows = total
				if total == len(self.rowlines[t][p]):
					standardphases.append((t, p))
		self.height = self.scaleH + (maxrows*self.rowH)
		self.bodyH = self.height - self.scaleH
		# if there is 1 line per row, draw them the standard way
		for t, p in standardphases:
			for i in sorted(self.rowheight[t][p]):
				self.rowheight[t][p][i] = self.bodyH/len(self.rowlines[t][p])
	def createZoomBox(self, mode='command', testcount=1):
		# Create bounding box, add buttons
		html_zoombox = '<center><button id="zoomin">ZOOM IN +</button><button id="zoomout">ZOOM OUT -</button><button id="zoomdef">ZOOM 1:1</button></center>\n'
		html_timeline = '<div id="dmesgzoombox" class="zoombox">\n<div id="{0}" class="timeline" style="height:{1}px">\n'
		html_devlist1 = '<button id="devlist1" class="devlist" style="float:left;">Device Detail{0}</button>'
		html_devlist2 = '<button id="devlist2" class="devlist" style="float:right;">Device Detail2</button>\n'
		if mode != 'command':
			if testcount > 1:
				self.html += html_devlist2
				self.html += html_devlist1.format('1')
			else:
				self.html += html_devlist1.format('')
		self.html += html_zoombox
		self.html += html_timeline.format('dmesg', self.height)
	# Function: createTimeScale
	# Description:
	#	 Create the timescale for a timeline block
	# Arguments:
	#	 m0: start time (mode begin)
	#	 mMax: end time (mode end)
	#	 tTotal: total timeline time
	#	 mode: suspend or resume
	# Output:
	#	 The html code needed to display the time scale
	def createTimeScale(self, m0, mMax, tTotal, mode):
		timescale = '<div class="t" style="right:{0}%">{1}</div>\n'
		rline = '<div class="t" style="left:0;border-left:1px solid black;border-right:0;">{0}</div>\n'
		output = '<div class="timescale">\n'
		# set scale for timeline
		mTotal = mMax - m0
		tS = 0.1
		if(tTotal <= 0):
			return output+'</div>\n'
		if(tTotal > 4):
			tS = 1
		divTotal = int(mTotal/tS) + 1
		divEdge = (mTotal - tS*(divTotal-1))*100/mTotal
		for i in range(divTotal):
			htmlline = ''
			if(mode == 'suspend'):
				pos = '%0.3f' % (100 - ((float(i)*tS*100)/mTotal) - divEdge)
				val = '%0.fms' % (float(i-divTotal+1)*tS*1000)
				if(i == divTotal - 1):
					val = mode
				htmlline = timescale.format(pos, val)
			else:
				pos = '%0.3f' % (100 - ((float(i)*tS*100)/mTotal))
				val = '%0.fms' % (float(i)*tS*1000)
				htmlline = timescale.format(pos, val)
				if(i == 0):
					htmlline = rline.format(mode)
			output += htmlline
		self.html += output+'</div>\n'

# Class: TestProps
# Description:
#	 A list of values describing the properties of these test runs
class TestProps:
	stamp = ''
	sysinfo = ''
	cmdline = ''
	kparams = ''
	battery = ''
	fwdata = []
	stampfmt = '# [a-z]*-(?P<m>[0-9]{2})(?P<d>[0-9]{2})(?P<y>[0-9]{2})-'+\
				'(?P<H>[0-9]{2})(?P<M>[0-9]{2})(?P<S>[0-9]{2})'+\
				' (?P<host>.*) (?P<mode>.*) (?P<kernel>.*)$'
	batteryfmt = '^# battery (?P<a1>\w*) (?P<c1>\d*) (?P<a2>\w*) (?P<c2>\d*)'
	sysinfofmt = '^# sysinfo .*'
	cmdlinefmt = '^# command \| (?P<cmd>.*)'
	kparamsfmt = '^# kparams \| (?P<kp>.*)'
	devpropfmt = '# Device Properties: .*'
	tracertypefmt = '# tracer: (?P<t>.*)'
	firmwarefmt = '# fwsuspend (?P<s>[0-9]*) fwresume (?P<r>[0-9]*)$'
	procexecfmt = 'ps - (?P<ps>.*)$'
	ftrace_line_fmt_fg = \
		'^ *(?P<time>[0-9\.]*) *\| *(?P<cpu>[0-9]*)\)'+\
		' *(?P<proc>.*)-(?P<pid>[0-9]*) *\|'+\
		'[ +!#\*@$]*(?P<dur>[0-9\.]*) .*\|  (?P<msg>.*)'
	ftrace_line_fmt_nop = \
		' *(?P<proc>.*)-(?P<pid>[0-9]*) *\[(?P<cpu>[0-9]*)\] *'+\
		'(?P<flags>.{4}) *(?P<time>[0-9\.]*): *'+\
		'(?P<msg>.*)'
	ftrace_line_fmt = ftrace_line_fmt_nop
	cgformat = False
	data = 0
	ktemp = dict()
	def __init__(self):
		self.ktemp = dict()
	def setTracerType(self, tracer):
		if(tracer == 'function_graph'):
			self.cgformat = True
			self.ftrace_line_fmt = self.ftrace_line_fmt_fg
		elif(tracer == 'nop'):
			self.ftrace_line_fmt = self.ftrace_line_fmt_nop
		else:
			doError('Invalid tracer format: [%s]' % tracer)
	def parseStamp(self, data, sv):
		m = re.match(self.stampfmt, self.stamp)
		data.stamp = {'time': '', 'host': '', 'mode': ''}
		dt = datetime(int(m.group('y'))+2000, int(m.group('m')),
			int(m.group('d')), int(m.group('H')), int(m.group('M')),
			int(m.group('S')))
		data.stamp['time'] = dt.strftime('%B %d %Y, %I:%M:%S %p')
		data.stamp['host'] = m.group('host')
		data.stamp['mode'] = m.group('mode')
		data.stamp['kernel'] = m.group('kernel')
		data.stamp['app'] = sv.component
		data.stamp['url'] = \
			base64.b64decode('aHR0cDovL290Y3BsLW1hbmFnZXIuamYuaW50ZWwuY29tL2J1Z3ppbGxhL3Jlc3QuY2dp')
		m = re.match(self.batteryfmt, self.battery)
		if m:
			data.battery = m.groups()
		if re.match(self.sysinfofmt, self.sysinfo):
			for f in self.sysinfo.split('|'):
				if '#' in f:
					continue
				tmp = f.strip().split(':', 1)
				key = tmp[0]
				val = tmp[1]
				data.stamp[key] = val
		sv.hostname = data.stamp['host']
		sv.suspendmode = data.stamp['mode']
		if sv.suspendmode == 'command' and sv.ftracefile != '':
			modes = ['on', 'freeze', 'standby', 'mem', 'disk']
			fp = sysvals.openlog(sv.ftracefile, 'r')
			for line in fp:
				m = re.match('.* machine_suspend\[(?P<mode>.*)\]', line)
				if m and m.group('mode') in ['1', '2', '3', '4']:
					sv.suspendmode = modes[int(m.group('mode'))]
					data.stamp['mode'] = sv.suspendmode
					break
			fp.close()
		m = re.match(self.cmdlinefmt, self.cmdline)
		if m:
			sv.cmdline = m.group('cmd')
		if self.kparams:
			m = re.match(self.kparamsfmt, self.kparams)
			if m:
				sv.kparams = m.group('kp')
		if not sv.stamp:
			sv.stamp = data.stamp

# Class: TestRun
# Description:
#	 A container for a suspend/resume test run. This is necessary as
#	 there could be more than one, and they need to be separate.
class TestRun:
	ftemp = dict()
	ttemp = dict()
	data = 0
	def __init__(self, dataobj):
		self.data = dataobj
		self.ftemp = dict()
		self.ttemp = dict()

class ProcessMonitor:
	proclist = dict()
	running = False
	def procstat(self):
		c = ['cat /proc/[1-9]*/stat 2>/dev/null']
		process = Popen(c, shell=True, stdout=PIPE)
		running = dict()
		for line in process.stdout:
			data = line.split()
			pid = data[0]
			name = re.sub('[()]', '', data[1])
			user = int(data[13])
			kern = int(data[14])
			kjiff = ujiff = 0
			if pid not in self.proclist:
				self.proclist[pid] = {'name' : name, 'user' : user, 'kern' : kern}
			else:
				val = self.proclist[pid]
				ujiff = user - val['user']
				kjiff = kern - val['kern']
				val['user'] = user
				val['kern'] = kern
			if ujiff > 0 or kjiff > 0:
				running[pid] = ujiff + kjiff
		process.wait()
		out = ''
		for pid in running:
			jiffies = running[pid]
			val = self.proclist[pid]
			if out:
				out += ','
			out += '%s-%s %d' % (val['name'], pid, jiffies)
		return 'ps - '+out
	def processMonitor(self, tid):
		while self.running:
			out = self.procstat()
			if out:
				sysvals.fsetVal(out, 'trace_marker')
	def start(self):
		self.thread = Thread(target=self.processMonitor, args=(0,))
		self.running = True
		self.thread.start()
	def stop(self):
		self.running = False

# ----------------- FUNCTIONS --------------------

# Function: doesTraceLogHaveTraceEvents
# Description:
#	 Quickly determine if the ftrace log has all of the trace events,
#	 markers, and/or kprobes required for primary parsing.
def doesTraceLogHaveTraceEvents():
	kpcheck = ['_cal: (', '_cpu_down()']
	techeck = ['suspend_resume', 'device_pm_callback']
	tmcheck = ['SUSPEND START', 'RESUME COMPLETE']
	devcheck = []
	for i in sysvals.dev_tracefuncs:
		devcheck.append('%s_cal: (' % i)
	sysvals.usekprobes = False
	sysvals.extra = ''
	fp = sysvals.openlog(sysvals.ftracefile, 'r')
	for line in fp:
		# check for callgraph or dev mode data
		if not sysvals.extra:
			if 'tracer: function_graph' in line:
				sysvals.extra = 'callgraph'
				continue
			for i in devcheck:
				if i in line:
					sysvals.extra = 'dev'
		# check for kprobes
		if not sysvals.usekprobes:
			for i in kpcheck:
				if i in line:
					sysvals.usekprobes = True
		# check for all necessary trace events
		check = techeck[:]
		for i in techeck:
			if i in line:
				check.remove(i)
		techeck = check
		# check for all necessary trace markers
		check = tmcheck[:]
		for i in tmcheck:
			if i in line:
				check.remove(i)
		tmcheck = check
	fp.close()
	if len(techeck) == 0:
		sysvals.usetraceevents = True
	else:
		sysvals.usetraceevents = False
	if len(tmcheck) == 0:
		sysvals.usetracemarkers = True
	else:
		sysvals.usetracemarkers = False

# Function: appendIncompleteTraceLog
# Description:
#	 [deprecated for kernel 3.15 or newer]
#	 Adds callgraph data which lacks trace event data. This is only
#	 for timelines generated from 3.15 or older
# Arguments:
#	 testruns: the array of Data objects obtained from parseKernelLog
def appendIncompleteTraceLog(testruns):
	# create TestRun vessels for ftrace parsing
	testcnt = len(testruns)
	testidx = 0
	testrun = []
	for data in testruns:
		testrun.append(TestRun(data))

	# extract the callgraph and traceevent data
	sysvals.vprint('Analyzing the ftrace data (%s)...' % \
		os.path.basename(sysvals.ftracefile))
	tp = TestProps()
	tf = sysvals.openlog(sysvals.ftracefile, 'r')
	data = 0
	for line in tf:
		# remove any latent carriage returns
		line = line.replace('\r\n', '')
		# grab the stamp and sysinfo
		if re.match(tp.stampfmt, line):
			tp.stamp = line
			continue
		elif re.match(tp.sysinfofmt, line):
			tp.sysinfo = line
			continue
		elif re.match(tp.cmdlinefmt, line):
			tp.cmdline = line
			continue
		elif re.match(tp.batteryfmt, line):
			tp.battery = line
			continue
		# determine the trace data type (required for further parsing)
		m = re.match(tp.tracertypefmt, line)
		if(m):
			tp.setTracerType(m.group('t'))
			continue
		# device properties line
		if(re.match(tp.devpropfmt, line)):
			devProps(line)
			continue
		# parse only valid lines, if this is not one move on
		m = re.match(tp.ftrace_line_fmt, line)
		if(not m):
			continue
		# gather the basic message data from the line
		m_time = m.group('time')
		m_pid = m.group('pid')
		m_msg = m.group('msg')
		if(tp.cgformat):
			m_param3 = m.group('dur')
		else:
			m_param3 = 'traceevent'
		if(m_time and m_pid and m_msg):
			t = FTraceLine(m_time, m_msg, m_param3)
			pid = int(m_pid)
		else:
			continue
		# the line should be a call, return, or event
		if(not t.fcall and not t.freturn and not t.fevent):
			continue
		# look for the suspend start marker
		if(t.startMarker()):
			data = testrun[testidx].data
			tp.parseStamp(data, sysvals)
			data.setStart(t.time)
			continue
		if(not data):
			continue
		# find the end of resume
		if(t.endMarker()):
			data.setEnd(t.time)
			testidx += 1
			if(testidx >= testcnt):
				break
			continue
		# trace event processing
		if(t.fevent):
			continue
		# call/return processing
		elif sysvals.usecallgraph:
			# create a callgraph object for the data
			if(pid not in testrun[testidx].ftemp):
				testrun[testidx].ftemp[pid] = []
				testrun[testidx].ftemp[pid].append(FTraceCallGraph(pid, sysvals))
			# when the call is finished, see which device matches it
			cg = testrun[testidx].ftemp[pid][-1]
			res = cg.addLine(t)
			if(res != 0):
				testrun[testidx].ftemp[pid].append(FTraceCallGraph(pid, sysvals))
			if(res == -1):
				testrun[testidx].ftemp[pid][-1].addLine(t)
	tf.close()

	for test in testrun:
		# add the callgraph data to the device hierarchy
		for pid in test.ftemp:
			for cg in test.ftemp[pid]:
				if len(cg.list) < 1 or cg.invalid or (cg.end - cg.start == 0):
					continue
				if(not cg.postProcess()):
					id = 'task %s cpu %s' % (pid, m.group('cpu'))
					sysvals.vprint('Sanity check failed for '+\
						id+', ignoring this callback')
					continue
				callstart = cg.start
				callend = cg.end
				for p in test.data.sortedPhases():
					if(test.data.dmesg[p]['start'] <= callstart and
						callstart <= test.data.dmesg[p]['end']):
						list = test.data.dmesg[p]['list']
						for devname in list:
							dev = list[devname]
							if(pid == dev['pid'] and
								callstart <= dev['start'] and
								callend >= dev['end']):
								dev['ftrace'] = cg
						break

# Function: parseTraceLog
# Description:
#	 Analyze an ftrace log output file generated from this app during
#	 the execution phase. Used when the ftrace log is the primary data source
#	 and includes the suspend_resume and device_pm_callback trace events
#	 The ftrace filename is taken from sysvals
# Output:
#	 An array of Data objects
def parseTraceLog(live=False):
	sysvals.vprint('Analyzing the ftrace data (%s)...' % \
		os.path.basename(sysvals.ftracefile))
	if(os.path.exists(sysvals.ftracefile) == False):
		doError('%s does not exist' % sysvals.ftracefile)
	if not live:
		sysvals.setupAllKprobes()
	tracewatch = []
	if sysvals.usekprobes:
		tracewatch += ['sync_filesystems', 'freeze_processes', 'syscore_suspend',
			'syscore_resume', 'resume_console', 'thaw_processes', 'CPU_ON',
			'CPU_OFF', 'timekeeping_freeze', 'acpi_suspend']

	# extract the callgraph and traceevent data
	tp = TestProps()
	testruns = []
	testdata = []
	testrun = 0
	data = 0
	tf = sysvals.openlog(sysvals.ftracefile, 'r')
	phase = 'suspend_prepare'
	for line in tf:
		# remove any latent carriage returns
		line = line.replace('\r\n', '')
		# stamp and sysinfo lines
		if re.match(tp.stampfmt, line):
			tp.stamp = line
			continue
		elif re.match(tp.sysinfofmt, line):
			tp.sysinfo = line
			continue
		elif re.match(tp.cmdlinefmt, line):
			tp.cmdline = line
			continue
		elif re.match(tp.batteryfmt, line):
			tp.battery = line
			continue
		# firmware line: pull out any firmware data
		m = re.match(tp.firmwarefmt, line)
		if(m):
			tp.fwdata.append((int(m.group('s')), int(m.group('r'))))
			continue
		# tracer type line: determine the trace data type
		m = re.match(tp.tracertypefmt, line)
		if(m):
			tp.setTracerType(m.group('t'))
			continue
		# device properties line
		if(re.match(tp.devpropfmt, line)):
			devProps(line)
			continue
		# ignore all other commented lines
		if line[0] == '#':
			continue
		# ftrace line: parse only valid lines
		m = re.match(tp.ftrace_line_fmt, line)
		if(not m):
			continue
		# gather the basic message data from the line
		m_time = m.group('time')
		m_proc = m.group('proc')
		m_pid = m.group('pid')
		m_msg = m.group('msg')
		if(tp.cgformat):
			m_param3 = m.group('dur')
		else:
			m_param3 = 'traceevent'
		if(m_time and m_pid and m_msg):
			t = FTraceLine(m_time, m_msg, m_param3)
			pid = int(m_pid)
		else:
			continue
		# the line should be a call, return, or event
		if(not t.fcall and not t.freturn and not t.fevent):
			continue
		# find the start of suspend
		if(t.startMarker()):
			data = Data(len(testdata))
			testdata.append(data)
			testrun = TestRun(data)
			testruns.append(testrun)
			tp.parseStamp(data, sysvals)
			data.setStart(t.time)
			phase = data.setPhase('suspend_prepare', t.time, True)
			continue
		if(not data):
			continue
		# process cpu exec line
		if t.type == 'tracing_mark_write':
			m = re.match(tp.procexecfmt, t.name)
			if(m):
				proclist = dict()
				for ps in m.group('ps').split(','):
					val = ps.split()
					if not val:
						continue
					name = val[0].replace('--', '-')
					proclist[name] = int(val[1])
				data.pstl[t.time] = proclist
				continue
		# find the end of resume
		if(t.endMarker()):
			dm = data.dmesg
			data.setEnd(t.time)
			data.initDevicegroups()
			# give suspend_prepare an end if needed
			if 'suspend_prepare' in dm and dm['suspend_prepare']['end'] < 0:
				dm['suspend_prepare']['end'] = t.time
			# assume resume machine ends at next phase start
			if 'resume_machine' in dm and dm['resume_machine']['end'] < 0:
				np = data.nextPhase('resume_machine', 1)
				if np:
					dm['resume_machine']['end'] = dm[np]['start']
			# if kernel resume end not found, assume its the end marker
			if data.tKernRes == 0.0:
				data.tKernRes = t.time
			# if kernel suspend start not found, assume its the end marker
			if data.tKernSus == 0.0:
				data.tKernSus = t.time
			# set resume complete to end at end marker
			if 'resume_complete' in dm:
				dm['resume_complete']['end'] = t.time
			# check the firmware data for validity
			if sysvals.suspendmode == 'mem' and len(tp.fwdata) > data.testnumber:
				data.fwSuspend, data.fwResume = tp.fwdata[data.testnumber]
				if(data.tSuspended != 0 and data.tResumed != 0 and \
					(data.fwSuspend > 0 or data.fwResume > 0)):
					data.fwValid = True
			if(not sysvals.usetracemarkers):
				# no trace markers? then quit and be sure to finish recording
				# the event we used to trigger resume end
				if(len(testrun.ttemp['thaw_processes']) > 0):
					# if an entry exists, assume this is its end
					testrun.ttemp['thaw_processes'][-1]['end'] = t.time
				break
			continue
		# trace event processing
		if(t.fevent):
			if(t.type == 'suspend_resume'):
				# suspend_resume trace events have two types, begin and end
				if(re.match('(?P<name>.*) begin$', t.name)):
					isbegin = True
				elif(re.match('(?P<name>.*) end$', t.name)):
					isbegin = False
				else:
					continue
				m = re.match('(?P<name>.*)\[(?P<val>[0-9]*)\] .*', t.name)
				if(m):
					val = m.group('val')
					if val == '0':
						name = m.group('name')
					else:
						name = m.group('name')+'['+val+']'
				else:
					m = re.match('(?P<name>.*) .*', t.name)
					name = m.group('name')
				# ignore these events
				if(name.split('[')[0] in tracewatch):
					continue
				# -- phase changes --
				# start of kernel suspend
				if(re.match('suspend_enter\[.*', t.name)):
					if(isbegin):
						data.tKernSus = t.time
					continue
				# suspend_prepare start
				elif(re.match('dpm_prepare\[.*', t.name)):
					phase = 'suspend_prepare'
					if not isbegin:
						data.setPhase(phase, t.time, isbegin)
					if isbegin and data.tKernSus == 0:
						data.tKernSus = t.time
					continue
				# suspend start
				elif(re.match('dpm_suspend\[.*', t.name)):
					phase = data.setPhase('suspend', t.time, isbegin)
					continue
				# suspend_late start
				elif(re.match('dpm_suspend_late\[.*', t.name)):
					phase = data.setPhase('suspend_late', t.time, isbegin)
					continue
				# suspend_noirq start
				elif(re.match('dpm_suspend_noirq\[.*', t.name)):
					phase = data.setPhase('suspend_noirq', t.time, isbegin)
					continue
				# suspend_machine/resume_machine
				elif(re.match('machine_suspend\[.*', t.name)):
					if(isbegin):
						lp = data.lastPhase()
						phase = data.setPhase('suspend_machine', data.dmesg[lp]['end'], True)
						data.setPhase(phase, t.time, False)
						if data.tSuspended == 0:
							data.tSuspended = t.time
					else:
						phase = data.setPhase('resume_machine', t.time, True)
						if(sysvals.suspendmode in ['mem', 'disk']):
							if 'suspend_machine' in data.dmesg:
								data.dmesg['suspend_machine']['end'] = t.time
							data.tSuspended = t.time
						if data.tResumed == 0:
							data.tResumed = t.time
					continue
				# resume_noirq start
				elif(re.match('dpm_resume_noirq\[.*', t.name)):
					phase = data.setPhase('resume_noirq', t.time, isbegin)
					continue
				# resume_early start
				elif(re.match('dpm_resume_early\[.*', t.name)):
					phase = data.setPhase('resume_early', t.time, isbegin)
					continue
				# resume start
				elif(re.match('dpm_resume\[.*', t.name)):
					phase = data.setPhase('resume', t.time, isbegin)
					continue
				# resume complete start
				elif(re.match('dpm_complete\[.*', t.name)):
					phase = data.setPhase('resume_complete', t.time, isbegin)
					continue
				# skip trace events inside devices calls
				if(not data.isTraceEventOutsideDeviceCalls(pid, t.time)):
					continue
				# global events (outside device calls) are graphed
				if(name not in testrun.ttemp):
					testrun.ttemp[name] = []
				if(isbegin):
					# create a new list entry
					testrun.ttemp[name].append(\
						{'begin': t.time, 'end': t.time, 'pid': pid})
				else:
					if(len(testrun.ttemp[name]) > 0):
						# if an entry exists, assume this is its end
						testrun.ttemp[name][-1]['end'] = t.time
			# device callback start
			elif(t.type == 'device_pm_callback_start'):
				if phase not in data.dmesg:
					continue
				m = re.match('(?P<drv>.*) (?P<d>.*), parent: *(?P<p>.*), .*',\
					t.name);
				if(not m):
					continue
				drv = m.group('drv')
				n = m.group('d')
				p = m.group('p')
				if(n and p):
					data.newAction(phase, n, pid, p, t.time, -1, drv)
					if pid not in data.devpids:
						data.devpids.append(pid)
			# device callback finish
			elif(t.type == 'device_pm_callback_end'):
				if phase not in data.dmesg:
					continue
				m = re.match('(?P<drv>.*) (?P<d>.*), err.*', t.name);
				if(not m):
					continue
				n = m.group('d')
				list = data.dmesg[phase]['list']
				if(n in list):
					dev = list[n]
					dev['length'] = t.time - dev['start']
					dev['end'] = t.time
		# kprobe event processing
		elif(t.fkprobe):
			kprobename = t.type
			kprobedata = t.name
			key = (kprobename, pid)
			# displayname is generated from kprobe data
			displayname = ''
			if(t.fcall):
				displayname = sysvals.kprobeDisplayName(kprobename, kprobedata)
				if not displayname:
					continue
				if(key not in tp.ktemp):
					tp.ktemp[key] = []
				tp.ktemp[key].append({
					'pid': pid,
					'begin': t.time,
					'end': t.time,
					'name': displayname,
					'cdata': kprobedata,
					'proc': m_proc,
				})
			elif(t.freturn):
				if(key not in tp.ktemp) or len(tp.ktemp[key]) < 1:
					continue
				e = tp.ktemp[key][-1]
				if e['begin'] < 0.0 or t.time - e['begin'] < 0.000001:
					tp.ktemp[key].pop()
				else:
					e['end'] = t.time
					e['rdata'] = kprobedata
				# end of kernel resume
				if(kprobename == 'pm_notifier_call_chain' or \
					kprobename == 'pm_restore_console'):
					if phase in data.dmesg:
						data.dmesg[phase]['end'] = t.time
					data.tKernRes = t.time

		# callgraph processing
		elif sysvals.usecallgraph:
			# create a callgraph object for the data
			key = (m_proc, pid)
			if(key not in testrun.ftemp):
				testrun.ftemp[key] = []
				testrun.ftemp[key].append(FTraceCallGraph(pid, sysvals))
			# when the call is finished, see which device matches it
			cg = testrun.ftemp[key][-1]
			res = cg.addLine(t)
			if(res != 0):
				testrun.ftemp[key].append(FTraceCallGraph(pid, sysvals))
			if(res == -1):
				testrun.ftemp[key][-1].addLine(t)
	tf.close()

	if sysvals.suspendmode == 'command':
		for test in testruns:
			for p in test.data.sortedPhases():
				if p == 'suspend_prepare':
					test.data.dmesg[p]['start'] = test.data.start
					test.data.dmesg[p]['end'] = test.data.end
				else:
					test.data.dmesg[p]['start'] = test.data.end
					test.data.dmesg[p]['end'] = test.data.end
			test.data.tSuspended = test.data.end
			test.data.tResumed = test.data.end
			test.data.fwValid = False

	# dev source and procmon events can be unreadable with mixed phase height
	if sysvals.usedevsrc or sysvals.useprocmon:
		sysvals.mixedphaseheight = False

	for i in range(len(testruns)):
		test = testruns[i]
		data = test.data
		# find the total time range for this test (begin, end)
		tlb, tle = data.start, data.end
		if i < len(testruns) - 1:
			tle = testruns[i+1].data.start
		# add the process usage data to the timeline
		if sysvals.useprocmon:
			data.createProcessUsageEvents()
		# add the traceevent data to the device hierarchy
		if(sysvals.usetraceevents):
			# add actual trace funcs
			for name in test.ttemp:
				for event in test.ttemp[name]:
					data.newActionGlobal(name, event['begin'], event['end'], event['pid'])
			# add the kprobe based virtual tracefuncs as actual devices
			for key in tp.ktemp:
				name, pid = key
				if name not in sysvals.tracefuncs:
					continue
				for e in tp.ktemp[key]:
					kb, ke = e['begin'], e['end']
					if kb == ke or tlb > kb or tle <= kb:
						continue
					color = sysvals.kprobeColor(name)
					data.newActionGlobal(e['name'], kb, ke, pid, color)
			# add config base kprobes and dev kprobes
			if sysvals.usedevsrc:
				for key in tp.ktemp:
					name, pid = key
					if name in sysvals.tracefuncs or name not in sysvals.dev_tracefuncs:
						continue
					for e in tp.ktemp[key]:
						kb, ke = e['begin'], e['end']
						if kb == ke or tlb > kb or tle <= kb:
							continue
						data.addDeviceFunctionCall(e['name'], name, e['proc'], pid, kb,
							ke, e['cdata'], e['rdata'])
		if sysvals.usecallgraph:
			# add the callgraph data to the device hierarchy
			sortlist = dict()
			for key in test.ftemp:
				proc, pid = key
				for cg in test.ftemp[key]:
					if len(cg.list) < 1 or cg.invalid or (cg.end - cg.start == 0):
						continue
					if(not cg.postProcess()):
						id = 'task %s' % (pid)
						sysvals.vprint('Sanity check failed for '+\
							id+', ignoring this callback')
						continue
					# match cg data to devices
					devname = ''
					if sysvals.suspendmode != 'command':
						devname = cg.deviceMatch(pid, data)
					if not devname:
						sortkey = '%f%f%d' % (cg.start, cg.end, pid)
						sortlist[sortkey] = cg
					elif len(cg.list) > 1000000:
						print 'WARNING: the callgraph for %s is massive (%d lines)' %\
							(devname, len(cg.list))
			# create blocks for orphan cg data
			for sortkey in sorted(sortlist):
				cg = sortlist[sortkey]
				name = cg.name
				if sysvals.isCallgraphFunc(name):
					sysvals.vprint('Callgraph found for task %d: %.3fms, %s' % (cg.pid, (cg.end - cg.start)*1000, name))
					cg.newActionFromFunction(data)
	if sysvals.suspendmode == 'command':
		return (testdata, '')

	# fill in any missing phases
	error = []
	for data in testdata:
		tn = '' if len(testdata) == 1 else ('%d' % (data.testnumber + 1))
		terr = ''
		phasedef = data.phasedef
		lp = 'suspend_prepare'
		for p in sorted(phasedef, key=lambda k:phasedef[k]['order']):
			if p not in data.dmesg:
				if not terr:
					print 'TEST%s FAILED: %s failed in %s phase' % (tn, sysvals.suspendmode, lp)
					terr = '%s%s failed in %s phase' % (sysvals.suspendmode, tn, lp)
					error.append(terr)
					if data.tSuspended == 0:
						data.tSuspended = data.dmesg[lp]['end']
					if data.tResumed == 0:
						data.tResumed = data.dmesg[lp]['end']
				sysvals.vprint('WARNING: phase "%s" is missing!' % p)
			lp = p
		lp = data.sortedPhases()[0]
		for p in data.sortedPhases():
			if(p != lp and not ('machine' in p and 'machine' in lp)):
				data.dmesg[lp]['end'] = data.dmesg[p]['start']
			lp = p
		if data.tSuspended == 0:
			data.tSuspended = data.tKernRes
		if data.tResumed == 0:
			data.tResumed = data.tSuspended

		if(len(sysvals.devicefilter) > 0):
			data.deviceFilter(sysvals.devicefilter)
		data.fixupInitcallsThatDidntReturn()
		if sysvals.usedevsrc:
			data.optimizeDevSrc()

	# x2: merge any overlapping devices between test runs
	if sysvals.usedevsrc and len(testdata) > 1:
		tc = len(testdata)
		for i in range(tc - 1):
			devlist = testdata[i].overflowDevices()
			for j in range(i + 1, tc):
				testdata[j].mergeOverlapDevices(devlist)
		testdata[0].stitchTouchingThreads(testdata[1:])
	return (testdata, ', '.join(error))

# Function: loadKernelLog
# Description:
#	 [deprecated for kernel 3.15.0 or newer]
#	 load the dmesg file into memory and fix up any ordering issues
#	 The dmesg filename is taken from sysvals
# Output:
#	 An array of empty Data objects with only their dmesgtext attributes set
def loadKernelLog():
	sysvals.vprint('Analyzing the dmesg data (%s)...' % \
		os.path.basename(sysvals.dmesgfile))
	if(os.path.exists(sysvals.dmesgfile) == False):
		doError('%s does not exist' % sysvals.dmesgfile)

	# there can be multiple test runs in a single file
	tp = TestProps()
	tp.stamp = datetime.now().strftime('# suspend-%m%d%y-%H%M%S localhost mem unknown')
	testruns = []
	data = 0
	lf = sysvals.openlog(sysvals.dmesgfile, 'r')
	for line in lf:
		line = line.replace('\r\n', '')
		idx = line.find('[')
		if idx > 1:
			line = line[idx:]
		# grab the stamp and sysinfo
		if re.match(tp.stampfmt, line):
			tp.stamp = line
			continue
		elif re.match(tp.sysinfofmt, line):
			tp.sysinfo = line
			continue
		elif re.match(tp.cmdlinefmt, line):
			tp.cmdline = line
			continue
		elif re.match(tp.batteryfmt, line):
			tp.battery = line
			continue
		m = re.match(tp.firmwarefmt, line)
		if(m):
			tp.fwdata.append((int(m.group('s')), int(m.group('r'))))
			continue
		m = re.match('[ \t]*(\[ *)(?P<ktime>[0-9\.]*)(\]) (?P<msg>.*)', line)
		if(not m):
			continue
		msg = m.group("msg")
		if(re.match('PM: Syncing filesystems.*', msg)):
			if(data):
				testruns.append(data)
			data = Data(len(testruns))
			tp.parseStamp(data, sysvals)
			if len(tp.fwdata) > data.testnumber:
				data.fwSuspend, data.fwResume = tp.fwdata[data.testnumber]
				if(data.fwSuspend > 0 or data.fwResume > 0):
					data.fwValid = True
		if(not data):
			continue
		m = re.match('.* *(?P<k>[0-9]\.[0-9]{2}\.[0-9]-.*) .*', msg)
		if(m):
			sysvals.stamp['kernel'] = m.group('k')
		m = re.match('PM: Preparing system for (?P<m>.*) sleep', msg)
		if(m):
			sysvals.stamp['mode'] = sysvals.suspendmode = m.group('m')
		data.dmesgtext.append(line)
	lf.close()

	if data:
		testruns.append(data)
	if len(testruns) < 1:
		print('ERROR: dmesg log has no suspend/resume data: %s' \
			% sysvals.dmesgfile)

	# fix lines with same timestamp/function with the call and return swapped
	for data in testruns:
		last = ''
		for line in data.dmesgtext:
			mc = re.match('.*(\[ *)(?P<t>[0-9\.]*)(\]) calling  '+\
				'(?P<f>.*)\+ @ .*, parent: .*', line)
			mr = re.match('.*(\[ *)(?P<t>[0-9\.]*)(\]) call '+\
				'(?P<f>.*)\+ returned .* after (?P<dt>.*) usecs', last)
			if(mc and mr and (mc.group('t') == mr.group('t')) and
				(mc.group('f') == mr.group('f'))):
				i = data.dmesgtext.index(last)
				j = data.dmesgtext.index(line)
				data.dmesgtext[i] = line
				data.dmesgtext[j] = last
			last = line
	return testruns

# Function: parseKernelLog
# Description:
#	 [deprecated for kernel 3.15.0 or newer]
#	 Analyse a dmesg log output file generated from this app during
#	 the execution phase. Create a set of device structures in memory
#	 for subsequent formatting in the html output file
#	 This call is only for legacy support on kernels where the ftrace
#	 data lacks the suspend_resume or device_pm_callbacks trace events.
# Arguments:
#	 data: an empty Data object (with dmesgtext) obtained from loadKernelLog
# Output:
#	 The filled Data object
def parseKernelLog(data):
	phase = 'suspend_runtime'

	if(data.fwValid):
		sysvals.vprint('Firmware Suspend = %u ns, Firmware Resume = %u ns' % \
			(data.fwSuspend, data.fwResume))

	# dmesg phase match table
	dm = {
		'suspend_prepare': ['PM: Syncing filesystems.*'],
		        'suspend': ['PM: Entering [a-z]* sleep.*', 'Suspending console.*'],
		   'suspend_late': ['PM: suspend of devices complete after.*'],
		  'suspend_noirq': ['PM: late suspend of devices complete after.*'],
		'suspend_machine': ['PM: noirq suspend of devices complete after.*'],
		 'resume_machine': ['ACPI: Low-level resume complete.*'],
		   'resume_noirq': ['ACPI: Waking up from system sleep state.*'],
		   'resume_early': ['PM: noirq resume of devices complete after.*'],
		         'resume': ['PM: early resume of devices complete after.*'],
		'resume_complete': ['PM: resume of devices complete after.*'],
		    'post_resume': ['.*Restarting tasks \.\.\..*'],
	}
	if(sysvals.suspendmode == 'standby'):
		dm['resume_machine'] = ['PM: Restoring platform NVS memory']
	elif(sysvals.suspendmode == 'disk'):
		dm['suspend_late'] = ['PM: freeze of devices complete after.*']
		dm['suspend_noirq'] = ['PM: late freeze of devices complete after.*']
		dm['suspend_machine'] = ['PM: noirq freeze of devices complete after.*']
		dm['resume_machine'] = ['PM: Restoring platform NVS memory']
		dm['resume_early'] = ['PM: noirq restore of devices complete after.*']
		dm['resume'] = ['PM: early restore of devices complete after.*']
		dm['resume_complete'] = ['PM: restore of devices complete after.*']
	elif(sysvals.suspendmode == 'freeze'):
		dm['resume_machine'] = ['ACPI: resume from mwait']

	# action table (expected events that occur and show up in dmesg)
	at = {
		'sync_filesystems': {
			'smsg': 'PM: Syncing filesystems.*',
			'emsg': 'PM: Preparing system for mem sleep.*' },
		'freeze_user_processes': {
			'smsg': 'Freezing user space processes .*',
			'emsg': 'Freezing remaining freezable tasks.*' },
		'freeze_tasks': {
			'smsg': 'Freezing remaining freezable tasks.*',
			'emsg': 'PM: Entering (?P<mode>[a-z,A-Z]*) sleep.*' },
		'ACPI prepare': {
			'smsg': 'ACPI: Preparing to enter system sleep state.*',
			'emsg': 'PM: Saving platform NVS memory.*' },
		'PM vns': {
			'smsg': 'PM: Saving platform NVS memory.*',
			'emsg': 'Disabling non-boot CPUs .*' },
	}

	t0 = -1.0
	cpu_start = -1.0
	prevktime = -1.0
	actions = dict()
	for line in data.dmesgtext:
		# parse each dmesg line into the time and message
		m = re.match('[ \t]*(\[ *)(?P<ktime>[0-9\.]*)(\]) (?P<msg>.*)', line)
		if(m):
			val = m.group('ktime')
			try:
				ktime = float(val)
			except:
				continue
			msg = m.group('msg')
			# initialize data start to first line time
			if t0 < 0:
				data.setStart(ktime)
				t0 = ktime
		else:
			continue

		# check for a phase change line
		phasechange = False
		for p in dm:
			for s in dm[p]:
				if(re.match(s, msg)):
					phasechange, phase = True, p
					break

		# hack for determining resume_machine end for freeze
		if(not sysvals.usetraceevents and sysvals.suspendmode == 'freeze' \
			and phase == 'resume_machine' and \
			re.match('calling  (?P<f>.*)\+ @ .*, parent: .*', msg)):
			data.setPhase(phase, ktime, False)
			phase = 'resume_noirq'
			data.setPhase(phase, ktime, True)

		if phasechange:
			if phase == 'suspend_prepare':
				data.setPhase(phase, ktime, True)
				data.setStart(ktime)
				data.tKernSus = ktime
			elif phase == 'suspend':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'suspend_late':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'suspend_noirq':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'suspend_machine':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'resume_machine':
				lp = data.lastPhase()
				if(sysvals.suspendmode in ['freeze', 'standby']):
					data.tSuspended = prevktime
					if lp:
						data.setPhase(lp, prevktime, False)
				else:
					data.tSuspended = ktime
					if lp:
						data.setPhase(lp, prevktime, False)
				data.tResumed = ktime
				data.setPhase(phase, ktime, True)
			elif phase == 'resume_noirq':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'resume_early':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'resume':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'resume_complete':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setPhase(phase, ktime, True)
			elif phase == 'post_resume':
				lp = data.lastPhase()
				if lp:
					data.setPhase(lp, ktime, False)
				data.setEnd(ktime)
				data.tKernRes = ktime
				break

		# -- device callbacks --
		if(phase in data.sortedPhases()):
			# device init call
			if(re.match('calling  (?P<f>.*)\+ @ .*, parent: .*', msg)):
				sm = re.match('calling  (?P<f>.*)\+ @ '+\
					'(?P<n>.*), parent: (?P<p>.*)', msg);
				f = sm.group('f')
				n = sm.group('n')
				p = sm.group('p')
				if(f and n and p):
					data.newAction(phase, f, int(n), p, ktime, -1, '')
			# device init return
			elif(re.match('call (?P<f>.*)\+ returned .* after '+\
				'(?P<t>.*) usecs', msg)):
				sm = re.match('call (?P<f>.*)\+ returned .* after '+\
					'(?P<t>.*) usecs(?P<a>.*)', msg);
				f = sm.group('f')
				t = sm.group('t')
				list = data.dmesg[phase]['list']
				if(f in list):
					dev = list[f]
					dev['length'] = int(t)
					dev['end'] = ktime

		# if trace events are not available, these are better than nothing
		if(not sysvals.usetraceevents):
			# look for known actions
			for a in at:
				if(re.match(at[a]['smsg'], msg)):
					if(a not in actions):
						actions[a] = []
					actions[a].append({'begin': ktime, 'end': ktime})
				if(re.match(at[a]['emsg'], msg)):
					if(a in actions):
						actions[a][-1]['end'] = ktime
			# now look for CPU on/off events
			if(re.match('Disabling non-boot CPUs .*', msg)):
				# start of first cpu suspend
				cpu_start = ktime
			elif(re.match('Enabling non-boot CPUs .*', msg)):
				# start of first cpu resume
				cpu_start = ktime
			elif(re.match('smpboot: CPU (?P<cpu>[0-9]*) is now offline', msg)):
				# end of a cpu suspend, start of the next
				m = re.match('smpboot: CPU (?P<cpu>[0-9]*) is now offline', msg)
				cpu = 'CPU'+m.group('cpu')
				if(cpu not in actions):
					actions[cpu] = []
				actions[cpu].append({'begin': cpu_start, 'end': ktime})
				cpu_start = ktime
			elif(re.match('CPU(?P<cpu>[0-9]*) is up', msg)):
				# end of a cpu resume, start of the next
				m = re.match('CPU(?P<cpu>[0-9]*) is up', msg)
				cpu = 'CPU'+m.group('cpu')
				if(cpu not in actions):
					actions[cpu] = []
				actions[cpu].append({'begin': cpu_start, 'end': ktime})
				cpu_start = ktime
		prevktime = ktime
	data.initDevicegroups()

	# fill in any missing phases
	phasedef = data.phasedef
	terr, lp = '', 'suspend_prepare'
	for p in sorted(phasedef, key=lambda k:phasedef[k]['order']):
		if p not in data.dmesg:
			if not terr:
				print 'TEST FAILED: %s failed in %s phase' % (sysvals.suspendmode, lp)
				terr = '%s failed in %s phase' % (sysvals.suspendmode, lp)
				if data.tSuspended == 0:
					data.tSuspended = data.dmesg[lp]['end']
				if data.tResumed == 0:
					data.tResumed = data.dmesg[lp]['end']
			sysvals.vprint('WARNING: phase "%s" is missing!' % p)
		lp = p
	lp = data.sortedPhases()[0]
	for p in data.sortedPhases():
		if(p != lp and not ('machine' in p and 'machine' in lp)):
			data.dmesg[lp]['end'] = data.dmesg[p]['start']
		lp = p
	if data.tSuspended == 0:
		data.tSuspended = data.tKernRes
	if data.tResumed == 0:
		data.tResumed = data.tSuspended

	# fill in any actions we've found
	for name in actions:
		for event in actions[name]:
			data.newActionGlobal(name, event['begin'], event['end'])

	if(len(sysvals.devicefilter) > 0):
		data.deviceFilter(sysvals.devicefilter)
	data.fixupInitcallsThatDidntReturn()
	return True

def callgraphHTML(sv, hf, num, cg, title, color, devid):
	html_func_top = '<article id="{0}" class="atop" style="background:{1}">\n<input type="checkbox" class="pf" id="f{2}" checked/><label for="f{2}">{3} {4}</label>\n'
	html_func_start = '<article>\n<input type="checkbox" class="pf" id="f{0}" checked/><label for="f{0}">{1} {2}</label>\n'
	html_func_end = '</article>\n'
	html_func_leaf = '<article>{0} {1}</article>\n'

	cgid = devid
	if cg.id:
		cgid += cg.id
	cglen = (cg.end - cg.start) * 1000
	if cglen < sv.mincglen:
		return num

	fmt = '<r>(%.3f ms @ '+sv.timeformat+' to '+sv.timeformat+')</r>'
	flen = fmt % (cglen, cg.start, cg.end)
	hf.write(html_func_top.format(cgid, color, num, title, flen))
	num += 1
	for line in cg.list:
		if(line.length < 0.000000001):
			flen = ''
		else:
			fmt = '<n>(%.3f ms @ '+sv.timeformat+')</n>'
			flen = fmt % (line.length*1000, line.time)
		if line.isLeaf():
			hf.write(html_func_leaf.format(line.name, flen))
		elif line.freturn:
			hf.write(html_func_end)
		else:
			hf.write(html_func_start.format(num, line.name, flen))
			num += 1
	hf.write(html_func_end)
	return num

def addCallgraphs(sv, hf, data):
	hf.write('<section id="callgraphs" class="callgraph">\n')
	# write out the ftrace data converted to html
	num = 0
	for p in data.sortedPhases():
		if sv.cgphase and p != sv.cgphase:
			continue
		list = data.dmesg[p]['list']
		for devname in data.sortedDevices(p):
			if len(sv.cgfilter) > 0 and devname not in sv.cgfilter:
				continue
			dev = list[devname]
			color = 'white'
			if 'color' in data.dmesg[p]:
				color = data.dmesg[p]['color']
			if 'color' in dev:
				color = dev['color']
			name = devname
			if(devname in sv.devprops):
				name = sv.devprops[devname].altName(devname)
			if sv.suspendmode in suspendmodename:
				name += ' '+p
			if('ftrace' in dev):
				cg = dev['ftrace']
				num = callgraphHTML(sv, hf, num, cg,
					name, color, dev['id'])
			if('ftraces' in dev):
				for cg in dev['ftraces']:
					num = callgraphHTML(sv, hf, num, cg,
						name+' &rarr; '+cg.name, color, dev['id'])
	hf.write('\n\n    </section>\n')

# Function: createHTMLSummarySimple
# Description:
#	 Create summary html file for a series of tests
# Arguments:
#	 testruns: array of Data objects from parseTraceLog
def createHTMLSummarySimple(testruns, htmlfile, folder):
	# write the html header first (html head, css code, up to body start)
	html = '<!DOCTYPE html>\n<html>\n<head>\n\
	<meta http-equiv="content-type" content="text/html; charset=UTF-8">\n\
	<title>SleepGraph Summary</title>\n\
	<style type=\'text/css\'>\n\
		.stamp {width: 100%;text-align:center;background:#888;line-height:30px;color:white;font: 25px Arial;}\n\
		table {width:100%;border-collapse: collapse;}\n\
		.summary {border:1px solid;}\n\
		th {border: 1px solid black;background:#222;color:white;}\n\
		td {font: 14px "Times New Roman";text-align: center;}\n\
		tr.head td {border: 1px solid black;background:#aaa;}\n\
		tr.alt {background-color:#ddd;}\n\
		tr.notice {color:red;}\n\
		.minval {background-color:#BBFFBB;}\n\
		.medval {background-color:#BBBBFF;}\n\
		.maxval {background-color:#FFBBBB;}\n\
		.head a {color:#000;text-decoration: none;}\n\
	</style>\n</head>\n<body>\n'

	# extract the test data into list
	list = dict()
	tAvg, tMin, tMax, tMed = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [[], []]
	iMin, iMed, iMax = [0, 0], [0, 0], [0, 0]
	num = 0
	lastmode = ''
	cnt = dict()
	for data in sorted(testruns, key=lambda v:(v['mode'], v['host'], v['kernel'], v['time'])):
		mode = data['mode']
		if mode not in list:
			list[mode] = {'data': [], 'avg': [0,0], 'min': [0,0], 'max': [0,0], 'med': [0,0]}
		if lastmode and lastmode != mode and num > 0:
			for i in range(2):
				s = sorted(tMed[i])
				list[lastmode]['med'][i] = s[int(len(s)/2)]
				iMed[i] = tMed[i].index(list[lastmode]['med'][i])
			list[lastmode]['avg'] = [tAvg[0] / num, tAvg[1] / num]
			list[lastmode]['min'] = tMin
			list[lastmode]['max'] = tMax
			list[lastmode]['idx'] = (iMin, iMed, iMax)
			tAvg, tMin, tMax, tMed = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [[], []]
			iMin, iMed, iMax = [0, 0], [0, 0], [0, 0]
			num = 0
		tVal = [float(data['suspend']), float(data['resume'])]
		list[mode]['data'].append([data['host'], data['kernel'],
			data['time'], tVal[0], tVal[1], data['url'], data['result'],
			data['issues'], data['worst'], data['worsttime']])
		idx = len(list[mode]['data']) - 1
		if data['result'] not in cnt:
			cnt[data['result']] = 1
		else:
			cnt[data['result']] += 1
		if data['result'] == 'pass':
			for i in range(2):
				tMed[i].append(tVal[i])
				tAvg[i] += tVal[i]
				if tMin[i] == 0 or tVal[i] < tMin[i]:
					iMin[i] = idx
					tMin[i] = tVal[i]
				if tMax[i] == 0 or tVal[i] > tMax[i]:
					iMax[i] = idx
					tMax[i] = tVal[i]
			num += 1
		lastmode = mode
	if lastmode and num > 0:
		for i in range(2):
			s = sorted(tMed[i])
			list[lastmode]['med'][i] = s[int(len(s)/2)]
			iMed[i] = tMed[i].index(list[lastmode]['med'][i])
		list[lastmode]['avg'] = [tAvg[0] / num, tAvg[1] / num]
		list[lastmode]['min'] = tMin
		list[lastmode]['max'] = tMax
		list[lastmode]['idx'] = (iMin, iMed, iMax)

	# group test header
	desc = []
	for ilk in sorted(cnt, reverse=True):
		if cnt[ilk] > 0:
			desc.append('%d %s' % (cnt[ilk], ilk))
	html += '<div class="stamp">%s (%d tests: %s)</div>\n' % (folder, len(testruns), ', '.join(desc))
	th = '\t<th>{0}</th>\n'
	td = '\t<td>{0}</td>\n'
	tdh = '\t<td{1}>{0}</td>\n'
	tdlink = '\t<td><a href="{0}">html</a></td>\n'

	# table header
	html += '<table class="summary">\n<tr>\n' + th.format('#') +\
		th.format('Mode') + th.format('Host') + th.format('Kernel') +\
		th.format('Test Time') + th.format('Result') + th.format('Issues') +\
		th.format('Suspend') + th.format('Resume') + th.format('Worst Device') +\
		th.format('Worst Time') + th.format('Detail') + '</tr>\n'

	# export list into html
	head = '<tr class="head"><td>{0}</td><td>{1}</td>'+\
		'<td colspan=10 class="sus">Suspend Avg={2} '+\
		'<span class=minval><a href="#s{10}min">Min={3}</a></span> '+\
		'<span class=medval><a href="#s{10}med">Med={4}</a></span> '+\
		'<span class=maxval><a href="#s{10}max">Max={5}</a></span> '+\
		'Resume Avg={6} '+\
		'<span class=minval><a href="#r{10}min">Min={7}</a></span> '+\
		'<span class=medval><a href="#r{10}med">Med={8}</a></span> '+\
		'<span class=maxval><a href="#r{10}max">Max={9}</a></span></td>'+\
		'</tr>\n'
	headnone = '<tr class="head"><td>{0}</td><td>{1}</td><td colspan=10></td></tr>\n'
	for mode in list:
		# header line for each suspend mode
		num = 0
		tAvg, tMin, tMax, tMed = list[mode]['avg'], list[mode]['min'],\
			list[mode]['max'], list[mode]['med']
		count = len(list[mode]['data'])
		if 'idx' in list[mode]:
			iMin, iMed, iMax = list[mode]['idx']
			html += head.format('%d' % count, mode.upper(),
				'%.3f' % tAvg[0], '%.3f' % tMin[0], '%.3f' % tMed[0], '%.3f' % tMax[0],
				'%.3f' % tAvg[1], '%.3f' % tMin[1], '%.3f' % tMed[1], '%.3f' % tMax[1],
				mode.lower()
			)
		else:
			iMin = iMed = iMax = [-1, -1, -1]
			html += headnone.format('%d' % count, mode.upper())
		for d in list[mode]['data']:
			# row classes - alternate row color
			rcls = ['alt'] if num % 2 == 1 else []
			if d[6] != 'pass':
				rcls.append('notice')
			html += '<tr class="'+(' '.join(rcls))+'">\n' if len(rcls) > 0 else '<tr>\n'
			# figure out if the line has sus or res highlighted
			idx = list[mode]['data'].index(d)
			tHigh = ['', '']
			for i in range(2):
				tag = 's%s' % mode if i == 0 else 'r%s' % mode
				if idx == iMin[i]:
					tHigh[i] = ' id="%smin" class=minval title="Minimum"' % tag
				elif idx == iMax[i]:
					tHigh[i] = ' id="%smax" class=maxval title="Maximum"' % tag
				elif idx == iMed[i]:
					tHigh[i] = ' id="%smed" class=medval title="Median"' % tag
			html += td.format("%d" % (list[mode]['data'].index(d) + 1)) # row
			html += td.format(mode)										# mode
			html += td.format(d[0])										# host
			html += td.format(d[1])										# kernel
			html += td.format(d[2])										# time
			html += td.format(d[6])										# result
			html += td.format(d[7])										# issues
			html += tdh.format('%.3f ms' % d[3], tHigh[0]) if d[3] else td.format('')	# suspend
			html += tdh.format('%.3f ms' % d[4], tHigh[1]) if d[4] else td.format('')	# resume
			html += td.format(d[8])										# worst
			html += td.format('%.3f ms' % d[9])							# worst time
			html += tdlink.format(d[5]) if d[5] else td.format('')		# url
			html += '</tr>\n'
			num += 1

	# flush the data to file
	hf = open(htmlfile, 'w')
	hf.write(html+'</table>\n</body>\n</html>\n')
	hf.close()

def ordinal(value):
	suffix = 'th'
	if value < 10 or value > 19:
		if value % 10 == 1:
			suffix = 'st'
		elif value % 10 == 2:
			suffix = 'nd'
		elif value % 10 == 3:
			suffix = 'rd'
	return '%d%s' % (value, suffix)

# Function: createHTML
# Description:
#	 Create the output html file from the resident test data
# Arguments:
#	 testruns: array of Data objects from parseKernelLog or parseTraceLog
# Output:
#	 True if the html file was created, false if it failed
def createHTML(testruns, testfail):
	if len(testruns) < 1:
		print('ERROR: Not enough test data to build a timeline')
		return

	kerror = False
	for data in testruns:
		if data.kerror:
			kerror = True
		data.trimFreezeTime(testruns[-1].tSuspended)

	# html function templates
	html_error = '<div id="{1}" title="kernel error/warning" class="err" style="right:{0}%">{2}&rarr;</div>\n'
	html_traceevent = '<div title="{0}" class="traceevent{6}" style="left:{1}%;top:{2}px;height:{3}px;width:{4}%;line-height:{3}px;{7}">{5}</div>\n'
	html_cpuexec = '<div class="jiffie" style="left:{0}%;top:{1}px;height:{2}px;width:{3}%;background:{4};"></div>\n'
	html_timetotal = '<table class="time1">\n<tr>'\
		'<td class="green" title="{3}">{2} Suspend Time: <b>{0} ms</b></td>'\
		'<td class="yellow" title="{4}">{2} Resume Time: <b>{1} ms</b></td>'\
		'</tr>\n</table>\n'
	html_timetotal2 = '<table class="time1">\n<tr>'\
		'<td class="green" title="{4}">{3} Suspend Time: <b>{0} ms</b></td>'\
		'<td class="gray" title="time spent in low-power mode with clock running">'+sysvals.suspendmode+' time: <b>{1} ms</b></td>'\
		'<td class="yellow" title="{5}">{3} Resume Time: <b>{2} ms</b></td>'\
		'</tr>\n</table>\n'
	html_timetotal3 = '<table class="time1">\n<tr>'\
		'<td class="green">Execution Time: <b>{0} ms</b></td>'\
		'<td class="yellow">Command: <b>{1}</b></td>'\
		'</tr>\n</table>\n'
	html_timegroups = '<table class="time2">\n<tr>'\
		'<td class="green" title="time from kernel enter_state({5}) to firmware mode [kernel time only]">{4}Kernel Suspend: {0} ms</td>'\
		'<td class="purple">{4}Firmware Suspend: {1} ms</td>'\
		'<td class="purple">{4}Firmware Resume: {2} ms</td>'\
		'<td class="yellow" title="time from firmware mode to return from kernel enter_state({5}) [kernel time only]">{4}Kernel Resume: {3} ms</td>'\
		'</tr>\n</table>\n'
	html_fail = '<table class="testfail"><tr><td>{0}</td></tr></table>\n'

	# html format variables
	scaleH = 20
	if kerror:
		scaleH = 40

	# device timeline
	devtl = Timeline(30, scaleH)

	# write the test title and general info header
	urlparams = '&columnlist=short_desc%2Ccf_platform%2Ccf_cpu%2Ccf_kernel'\
		'%2Ccf_power_mode%2Ccf_suspend_time%2Ccf_resume_time'\
		'%2Ccf_worst_perf1%2Ccf_worst_perf2%2Ccf_worst_perf3'\
		'%2Ccf_datetime&order=cf_resume_time'
	devtl.createHeader(sysvals, testruns[0].stamp, urlparams)

	# Generate the header for this timeline
	for data in testruns:
		tTotal = data.end - data.start
		sktime, rktime = data.getTimeValues()
		if(tTotal == 0):
			doError('No timeline data')
		if(len(data.tLow) > 0):
			low_time = '|'.join(data.tLow)
		if sysvals.suspendmode == 'command':
			run_time = '%.0f'%((data.end-data.start)*1000)
			if sysvals.testcommand:
				testdesc = sysvals.testcommand
			else:
				testdesc = 'unknown'
			if(len(testruns) > 1):
				testdesc = ordinal(data.testnumber+1)+' '+testdesc
			thtml = html_timetotal3.format(run_time, testdesc)
			devtl.html += thtml
		elif data.fwValid:
			suspend_time = '%.0f'%(sktime + (data.fwSuspend/1000000.0))
			resume_time = '%.0f'%(rktime + (data.fwResume/1000000.0))
			testdesc1 = 'Total'
			testdesc2 = ''
			stitle = 'time from kernel enter_state(%s) to low-power mode [kernel & firmware time]' % sysvals.suspendmode
			rtitle = 'time from low-power mode to return from kernel enter_state(%s) [firmware & kernel time]' % sysvals.suspendmode
			if(len(testruns) > 1):
				testdesc1 = testdesc2 = ordinal(data.testnumber+1)
				testdesc2 += ' '
			if(len(data.tLow) == 0):
				thtml = html_timetotal.format(suspend_time, \
					resume_time, testdesc1, stitle, rtitle)
			else:
				thtml = html_timetotal2.format(suspend_time, low_time, \
					resume_time, testdesc1, stitle, rtitle)
			devtl.html += thtml
			sftime = '%.3f'%(data.fwSuspend / 1000000.0)
			rftime = '%.3f'%(data.fwResume / 1000000.0)
			devtl.html += html_timegroups.format('%.3f'%sktime, \
				sftime, rftime, '%.3f'%rktime, testdesc2, sysvals.suspendmode)
		else:
			suspend_time = '%.3f' % sktime
			resume_time = '%.3f' % rktime
			testdesc = 'Kernel'
			stitle = 'time from kernel enter_state(%s) to firmware mode [kernel time only]' % sysvals.suspendmode
			rtitle = 'time from firmware mode to return from kernel enter_state(%s) [kernel time only]' % sysvals.suspendmode
			if(len(testruns) > 1):
				testdesc = ordinal(data.testnumber+1)+' '+testdesc
			if(len(data.tLow) == 0):
				thtml = html_timetotal.format(suspend_time, \
					resume_time, testdesc, stitle, rtitle)
			else:
				thtml = html_timetotal2.format(suspend_time, low_time, \
					resume_time, testdesc, stitle, rtitle)
			devtl.html += thtml

	if testfail:
		devtl.html += html_fail.format(testfail)

	# time scale for potentially multiple datasets
	t0 = testruns[0].start
	tMax = testruns[-1].end
	tTotal = tMax - t0

	# determine the maximum number of rows we need to draw
	fulllist = []
	threadlist = []
	pscnt = 0
	devcnt = 0
	for data in testruns:
		data.selectTimelineDevices('%f', tTotal, sysvals.mindevlen)
		for group in data.devicegroups:
			devlist = []
			for phase in group:
				for devname in data.tdevlist[phase]:
					d = DevItem(data.testnumber, phase, data.dmesg[phase]['list'][devname])
					devlist.append(d)
					if d.isa('kth'):
						threadlist.append(d)
					else:
						if d.isa('ps'):
							pscnt += 1
						else:
							devcnt += 1
						fulllist.append(d)
			if sysvals.mixedphaseheight:
				devtl.getPhaseRows(devlist)
	if not sysvals.mixedphaseheight:
		if len(threadlist) > 0 and len(fulllist) > 0:
			if pscnt > 0 and devcnt > 0:
				msg = 'user processes & device pm callbacks'
			elif pscnt > 0:
				msg = 'user processes'
			else:
				msg = 'device pm callbacks'
			d = testruns[0].addHorizontalDivider(msg, testruns[-1].end)
			fulllist.insert(0, d)
		devtl.getPhaseRows(fulllist)
		if len(threadlist) > 0:
			d = testruns[0].addHorizontalDivider('asynchronous kernel threads', testruns[-1].end)
			threadlist.insert(0, d)
			devtl.getPhaseRows(threadlist, devtl.rows)
	devtl.calcTotalRows()

	# draw the full timeline
	devtl.createZoomBox(sysvals.suspendmode, len(testruns))
	for data in testruns:
		# draw each test run and block chronologically
		phases = {'suspend':[],'resume':[]}
		for phase in data.sortedPhases():
			if data.dmesg[phase]['start'] > data.tSuspended:
				phases['resume'].append(phase)
			else:
				phases['suspend'].append(phase)
		# now draw the actual timeline blocks
		for dir in phases:
			# draw suspend and resume blocks separately
			bname = '%s%d' % (dir[0], data.testnumber)
			if dir == 'suspend':
				m0 = data.start
				mMax = data.tSuspended
				left = '%f' % (((m0-t0)*100.0)/tTotal)
			else:
				m0 = data.tSuspended
				mMax = data.end
				# in an x2 run, remove any gap between blocks
				if len(testruns) > 1 and data.testnumber == 0:
					mMax = testruns[1].start
				left = '%f' % ((((m0-t0)*100.0)+sysvals.srgap/2)/tTotal)
			mTotal = mMax - m0
			# if a timeline block is 0 length, skip altogether
			if mTotal == 0:
				continue
			width = '%f' % (((mTotal*100.0)-sysvals.srgap/2)/tTotal)
			devtl.html += devtl.html_tblock.format(bname, left, width, devtl.scaleH)
			for b in phases[dir]:
				# draw the phase color background
				phase = data.dmesg[b]
				length = phase['end']-phase['start']
				left = '%f' % (((phase['start']-m0)*100.0)/mTotal)
				width = '%f' % ((length*100.0)/mTotal)
				devtl.html += devtl.html_phase.format(left, width, \
					'%.3f'%devtl.scaleH, '%.3f'%devtl.bodyH, \
					data.dmesg[b]['color'], '')
			for e in data.errorinfo[dir]:
				# draw red lines for any kernel errors found
				type, t, idx1, idx2 = e
				id = '%d_%d' % (idx1, idx2)
				right = '%f' % (((mMax-t)*100.0)/mTotal)
				devtl.html += html_error.format(right, id, type)
			for b in phases[dir]:
				# draw the devices for this phase
				phaselist = data.dmesg[b]['list']
				for d in data.tdevlist[b]:
					name = d
					drv = ''
					dev = phaselist[d]
					xtraclass = ''
					xtrainfo = ''
					xtrastyle = ''
					if 'htmlclass' in dev:
						xtraclass = dev['htmlclass']
					if 'color' in dev:
						xtrastyle = 'background:%s;' % dev['color']
					if(d in sysvals.devprops):
						name = sysvals.devprops[d].altName(d)
						xtraclass = sysvals.devprops[d].xtraClass()
						xtrainfo = sysvals.devprops[d].xtraInfo()
					elif xtraclass == ' kth':
						xtrainfo = ' kernel_thread'
					if('drv' in dev and dev['drv']):
						drv = ' {%s}' % dev['drv']
					rowheight = devtl.phaseRowHeight(data.testnumber, b, dev['row'])
					rowtop = devtl.phaseRowTop(data.testnumber, b, dev['row'])
					top = '%.3f' % (rowtop + devtl.scaleH)
					left = '%f' % (((dev['start']-m0)*100)/mTotal)
					width = '%f' % (((dev['end']-dev['start'])*100)/mTotal)
					length = ' (%0.3f ms) ' % ((dev['end']-dev['start'])*1000)
					title = name+drv+xtrainfo+length
					if sysvals.suspendmode == 'command':
						title += sysvals.testcommand
					elif xtraclass == ' ps':
						if 'suspend' in b:
							title += 'pre_suspend_process'
						else:
							title += 'post_resume_process'
					else:
						title += b
					devtl.html += devtl.html_device.format(dev['id'], \
						title, left, top, '%.3f'%rowheight, width, \
						d+drv, xtraclass, xtrastyle)
					if('cpuexec' in dev):
						for t in sorted(dev['cpuexec']):
							start, end = t
							j = float(dev['cpuexec'][t]) / 5
							if j > 1.0:
								j = 1.0
							height = '%.3f' % (rowheight/3)
							top = '%.3f' % (rowtop + devtl.scaleH + 2*rowheight/3)
							left = '%f' % (((start-m0)*100)/mTotal)
							width = '%f' % ((end-start)*100/mTotal)
							color = 'rgba(255, 0, 0, %f)' % j
							devtl.html += \
								html_cpuexec.format(left, top, height, width, color)
					if('src' not in dev):
						continue
					# draw any trace events for this device
					for e in dev['src']:
						height = '%.3f' % devtl.rowH
						top = '%.3f' % (rowtop + devtl.scaleH + (e.row*devtl.rowH))
						left = '%f' % (((e.time-m0)*100)/mTotal)
						width = '%f' % (e.length*100/mTotal)
						xtrastyle = ''
						if e.color:
							xtrastyle = 'background:%s;' % e.color
						devtl.html += \
							html_traceevent.format(e.title(), \
								left, top, height, width, e.text(), '', xtrastyle)
			# draw the time scale, try to make the number of labels readable
			devtl.createTimeScale(m0, mMax, tTotal, dir)
			devtl.html += '</div>\n'

	# timeline is finished
	devtl.html += '</div>\n</div>\n'

	# draw a legend which describes the phases by color
	if sysvals.suspendmode != 'command':
		phasedef = testruns[-1].phasedef
		devtl.html += '<div class="legend">\n'
		pdelta = 100.0/len(phasedef.keys())
		pmargin = pdelta / 4.0
		for phase in sorted(phasedef, key=lambda k:phasedef[k]['order']):
			id, p = '', phasedef[phase]
			for word in phase.split('_'):
				id += word[0]
			order = '%.2f' % ((p['order'] * pdelta) + pmargin)
			name = string.replace(phase, '_', ' &nbsp;')
			devtl.html += devtl.html_legend.format(order, p['color'], name, id)
		devtl.html += '</div>\n'

	hf = open(sysvals.htmlfile, 'w')
	addCSS(hf, sysvals, len(testruns), kerror)

	# write the device timeline
	hf.write(devtl.html)
	hf.write('<div id="devicedetailtitle"></div>\n')
	hf.write('<div id="devicedetail" style="display:none;">\n')
	# draw the colored boxes for the device detail section
	for data in testruns:
		hf.write('<div id="devicedetail%d">\n' % data.testnumber)
		pscolor = 'linear-gradient(to top left, #ccc, #eee)'
		hf.write(devtl.html_phaselet.format('pre_suspend_process', \
			'0', '0', pscolor))
		for b in data.sortedPhases():
			phase = data.dmesg[b]
			length = phase['end']-phase['start']
			left = '%.3f' % (((phase['start']-t0)*100.0)/tTotal)
			width = '%.3f' % ((length*100.0)/tTotal)
			hf.write(devtl.html_phaselet.format(b, left, width, \
				data.dmesg[b]['color']))
		hf.write(devtl.html_phaselet.format('post_resume_process', \
			'0', '0', pscolor))
		if sysvals.suspendmode == 'command':
			hf.write(devtl.html_phaselet.format('cmdexec', '0', '0', pscolor))
		hf.write('</div>\n')
	hf.write('</div>\n')

	# write the ftrace data (callgraph)
	if sysvals.cgtest >= 0 and len(testruns) > sysvals.cgtest:
		data = testruns[sysvals.cgtest]
	else:
		data = testruns[-1]
	if sysvals.usecallgraph:
		addCallgraphs(sysvals, hf, data)

	# add the test log as a hidden div
	if sysvals.testlog and sysvals.logmsg:
		hf.write('<div id="testlog" style="display:none;">\n'+sysvals.logmsg+'</div>\n')
	# add the dmesg log as a hidden div
	if sysvals.dmesglog and sysvals.dmesgfile:
		hf.write('<div id="dmesglog" style="display:none;">\n')
		lf = sysvals.openlog(sysvals.dmesgfile, 'r')
		for line in lf:
			line = line.replace('<', '&lt').replace('>', '&gt')
			hf.write(line)
		lf.close()
		hf.write('</div>\n')
	# add the ftrace log as a hidden div
	if sysvals.ftracelog and sysvals.ftracefile:
		hf.write('<div id="ftracelog" style="display:none;">\n')
		lf = sysvals.openlog(sysvals.ftracefile, 'r')
		for line in lf:
			hf.write(line)
		lf.close()
		hf.write('</div>\n')

	# write the footer and close
	addScriptCode(hf, testruns)
	hf.write('</body>\n</html>\n')
	hf.close()
	return True

def addCSS(hf, sv, testcount=1, kerror=False, extra=''):
	kernel = sv.stamp['kernel']
	host = sv.hostname[0].upper()+sv.hostname[1:]
	mode = sv.suspendmode
	if sv.suspendmode in suspendmodename:
		mode = suspendmodename[sv.suspendmode]
	title = host+' '+mode+' '+kernel

	# various format changes by flags
	cgchk = 'checked'
	cgnchk = 'not(:checked)'
	if sv.cgexp:
		cgchk = 'not(:checked)'
		cgnchk = 'checked'

	hoverZ = 'z-index:8;'
	if sv.usedevsrc:
		hoverZ = ''

	devlistpos = 'absolute'
	if testcount > 1:
		devlistpos = 'relative'

	scaleTH = 20
	if kerror:
		scaleTH = 60

	# write the html header first (html head, css code, up to body start)
	html_header = '<!DOCTYPE html>\n<html>\n<head>\n\
	<meta http-equiv="content-type" content="text/html; charset=UTF-8">\n\
	<title>'+title+'</title>\n\
	<style type=\'text/css\'>\n\
		body {overflow-y:scroll;}\n\
		.stamp {width:100%;text-align:center;background:gray;line-height:30px;color:white;font:25px Arial;}\n\
		.stamp.sysinfo {font:10px Arial;}\n\
		.callgraph {margin-top:30px;box-shadow:5px 5px 20px black;}\n\
		.callgraph article * {padding-left:28px;}\n\
		h1 {color:black;font:bold 30px Times;}\n\
		t0 {color:black;font:bold 30px Times;}\n\
		t1 {color:black;font:30px Times;}\n\
		t2 {color:black;font:25px Times;}\n\
		t3 {color:black;font:20px Times;white-space:nowrap;}\n\
		t4 {color:black;font:bold 30px Times;line-height:60px;white-space:nowrap;}\n\
		cS {font:bold 13px Times;}\n\
		table {width:100%;}\n\
		.gray {background:rgba(80,80,80,0.1);}\n\
		.green {background:rgba(204,255,204,0.4);}\n\
		.purple {background:rgba(128,0,128,0.2);}\n\
		.yellow {background:rgba(255,255,204,0.4);}\n\
		.blue {background:rgba(169,208,245,0.4);}\n\
		.time1 {font:22px Arial;border:1px solid;}\n\
		.time2 {font:15px Arial;border-bottom:1px solid;border-left:1px solid;border-right:1px solid;}\n\
		.testfail {font:bold 22px Arial;color:red;border:1px dashed;}\n\
		td {text-align:center;}\n\
		r {color:#500000;font:15px Tahoma;}\n\
		n {color:#505050;font:15px Tahoma;}\n\
		.tdhl {color:red;}\n\
		.hide {display:none;}\n\
		.pf {display:none;}\n\
		.pf:'+cgchk+' + label {background:url(\'data:image/svg+xml;utf,<?xml version="1.0" standalone="no"?><svg xmlns="http://www.w3.org/2000/svg" height="18" width="18" version="1.1"><circle cx="9" cy="9" r="8" stroke="black" stroke-width="1" fill="white"/><rect x="4" y="8" width="10" height="2" style="fill:black;stroke-width:0"/><rect x="8" y="4" width="2" height="10" style="fill:black;stroke-width:0"/></svg>\') no-repeat left center;}\n\
		.pf:'+cgnchk+' ~ label {background:url(\'data:image/svg+xml;utf,<?xml version="1.0" standalone="no"?><svg xmlns="http://www.w3.org/2000/svg" height="18" width="18" version="1.1"><circle cx="9" cy="9" r="8" stroke="black" stroke-width="1" fill="white"/><rect x="4" y="8" width="10" height="2" style="fill:black;stroke-width:0"/></svg>\') no-repeat left center;}\n\
		.pf:'+cgchk+' ~ *:not(:nth-child(2)) {display:none;}\n\
		.zoombox {position:relative;width:100%;overflow-x:scroll;-webkit-user-select:none;-moz-user-select:none;user-select:none;}\n\
		.timeline {position:relative;font-size:14px;cursor:pointer;width:100%; overflow:hidden;background:linear-gradient(#cccccc, white);}\n\
		.thread {position:absolute;height:0%;overflow:hidden;z-index:7;line-height:30px;font-size:14px;border:1px solid;text-align:center;white-space:nowrap;}\n\
		.thread.ps {border-radius:3px;background:linear-gradient(to top, #ccc, #eee);}\n\
		.thread:hover {background:white;border:1px solid red;'+hoverZ+'}\n\
		.thread.sec,.thread.sec:hover {background:black;border:0;color:white;line-height:15px;font-size:10px;}\n\
		.hover {background:white;border:1px solid red;'+hoverZ+'}\n\
		.hover.sync {background:white;}\n\
		.hover.bg,.hover.kth,.hover.sync,.hover.ps {background:white;}\n\
		.jiffie {position:absolute;pointer-events: none;z-index:8;}\n\
		.traceevent {position:absolute;font-size:10px;z-index:7;overflow:hidden;color:black;text-align:center;white-space:nowrap;border-radius:5px;border:1px solid black;background:linear-gradient(to bottom right,#CCC,#969696);}\n\
		.traceevent:hover {color:white;font-weight:bold;border:1px solid white;}\n\
		.phase {position:absolute;overflow:hidden;border:0px;text-align:center;}\n\
		.phaselet {float:left;overflow:hidden;border:0px;text-align:center;min-height:100px;font-size:24px;}\n\
		.t {position:absolute;line-height:'+('%d'%scaleTH)+'px;pointer-events:none;top:0;height:100%;border-right:1px solid black;z-index:6;}\n\
		.err {position:absolute;top:0%;height:100%;border-right:3px solid red;color:red;font:bold 14px Times;line-height:18px;}\n\
		.legend {position:relative; width:100%; height:40px; text-align:center;margin-bottom:20px}\n\
		.legend .square {position:absolute;cursor:pointer;top:10px; width:0px;height:20px;border:1px solid;padding-left:20px;}\n\
		button {height:40px;width:200px;margin-bottom:20px;margin-top:20px;font-size:24px;}\n\
		.btnfmt {position:relative;float:right;height:25px;width:auto;margin-top:3px;margin-bottom:0;font-size:10px;text-align:center;}\n\
		.devlist {position:'+devlistpos+';width:190px;}\n\
		a:link {color:white;text-decoration:none;}\n\
		a:visited {color:white;}\n\
		a:hover {color:white;}\n\
		a:active {color:white;}\n\
		.version {position:relative;float:left;color:white;font-size:10px;line-height:30px;margin-left:10px;}\n\
		#devicedetail {min-height:100px;box-shadow:5px 5px 20px black;}\n\
		.tblock {position:absolute;height:100%;background:#ddd;}\n\
		.tback {position:absolute;width:100%;background:linear-gradient(#ccc, #ddd);}\n\
		.bg {z-index:1;}\n\
'+extra+'\
	</style>\n</head>\n<body>\n'
	hf.write(html_header)

# Function: addScriptCode
# Description:
#	 Adds the javascript code to the output html
# Arguments:
#	 hf: the open html file pointer
#	 testruns: array of Data objects from parseKernelLog or parseTraceLog
def addScriptCode(hf, testruns):
	t0 = testruns[0].start * 1000
	tMax = testruns[-1].end * 1000
	# create an array in javascript memory with the device details
	detail = '	var devtable = [];\n'
	for data in testruns:
		topo = data.deviceTopology()
		detail += '	devtable[%d] = "%s";\n' % (data.testnumber, topo)
	detail += '	var bounds = [%f,%f];\n' % (t0, tMax)
	# add the code which will manipulate the data in the browser
	script_code = \
	'<script type="text/javascript">\n'+detail+\
	'	var resolution = -1;\n'\
	'	var dragval = [0, 0];\n'\
	'	function redrawTimescale(t0, tMax, tS) {\n'\
	'		var rline = \'<div class="t" style="left:0;border-left:1px solid black;border-right:0;">\';\n'\
	'		var tTotal = tMax - t0;\n'\
	'		var list = document.getElementsByClassName("tblock");\n'\
	'		for (var i = 0; i < list.length; i++) {\n'\
	'			var timescale = list[i].getElementsByClassName("timescale")[0];\n'\
	'			var m0 = t0 + (tTotal*parseFloat(list[i].style.left)/100);\n'\
	'			var mTotal = tTotal*parseFloat(list[i].style.width)/100;\n'\
	'			var mMax = m0 + mTotal;\n'\
	'			var html = "";\n'\
	'			var divTotal = Math.floor(mTotal/tS) + 1;\n'\
	'			if(divTotal > 1000) continue;\n'\
	'			var divEdge = (mTotal - tS*(divTotal-1))*100/mTotal;\n'\
	'			var pos = 0.0, val = 0.0;\n'\
	'			for (var j = 0; j < divTotal; j++) {\n'\
	'				var htmlline = "";\n'\
	'				var mode = list[i].id[5];\n'\
	'				if(mode == "s") {\n'\
	'					pos = 100 - (((j)*tS*100)/mTotal) - divEdge;\n'\
	'					val = (j-divTotal+1)*tS;\n'\
	'					if(j == divTotal - 1)\n'\
	'						htmlline = \'<div class="t" style="right:\'+pos+\'%"><cS>S&rarr;</cS></div>\';\n'\
	'					else\n'\
	'						htmlline = \'<div class="t" style="right:\'+pos+\'%">\'+val+\'ms</div>\';\n'\
	'				} else {\n'\
	'					pos = 100 - (((j)*tS*100)/mTotal);\n'\
	'					val = (j)*tS;\n'\
	'					htmlline = \'<div class="t" style="right:\'+pos+\'%">\'+val+\'ms</div>\';\n'\
	'					if(j == 0)\n'\
	'						if(mode == "r")\n'\
	'							htmlline = rline+"<cS>&larr;R</cS></div>";\n'\
	'						else\n'\
	'							htmlline = rline+"<cS>0ms</div>";\n'\
	'				}\n'\
	'				html += htmlline;\n'\
	'			}\n'\
	'			timescale.innerHTML = html;\n'\
	'		}\n'\
	'	}\n'\
	'	function zoomTimeline() {\n'\
	'		var dmesg = document.getElementById("dmesg");\n'\
	'		var zoombox = document.getElementById("dmesgzoombox");\n'\
	'		var left = zoombox.scrollLeft;\n'\
	'		var val = parseFloat(dmesg.style.width);\n'\
	'		var newval = 100;\n'\
	'		var sh = window.outerWidth / 2;\n'\
	'		if(this.id == "zoomin") {\n'\
	'			newval = val * 1.2;\n'\
	'			if(newval > 910034) newval = 910034;\n'\
	'			dmesg.style.width = newval+"%";\n'\
	'			zoombox.scrollLeft = ((left + sh) * newval / val) - sh;\n'\
	'		} else if (this.id == "zoomout") {\n'\
	'			newval = val / 1.2;\n'\
	'			if(newval < 100) newval = 100;\n'\
	'			dmesg.style.width = newval+"%";\n'\
	'			zoombox.scrollLeft = ((left + sh) * newval / val) - sh;\n'\
	'		} else {\n'\
	'			zoombox.scrollLeft = 0;\n'\
	'			dmesg.style.width = "100%";\n'\
	'		}\n'\
	'		var tS = [10000, 5000, 2000, 1000, 500, 200, 100, 50, 20, 10, 5, 2, 1];\n'\
	'		var t0 = bounds[0];\n'\
	'		var tMax = bounds[1];\n'\
	'		var tTotal = tMax - t0;\n'\
	'		var wTotal = tTotal * 100.0 / newval;\n'\
	'		var idx = 7*window.innerWidth/1100;\n'\
	'		for(var i = 0; (i < tS.length)&&((wTotal / tS[i]) < idx); i++);\n'\
	'		if(i >= tS.length) i = tS.length - 1;\n'\
	'		if(tS[i] == resolution) return;\n'\
	'		resolution = tS[i];\n'\
	'		redrawTimescale(t0, tMax, tS[i]);\n'\
	'	}\n'\
	'	function deviceName(title) {\n'\
	'		var name = title.slice(0, title.indexOf(" ("));\n'\
	'		return name;\n'\
	'	}\n'\
	'	function deviceHover() {\n'\
	'		var name = deviceName(this.title);\n'\
	'		var dmesg = document.getElementById("dmesg");\n'\
	'		var dev = dmesg.getElementsByClassName("thread");\n'\
	'		var cpu = -1;\n'\
	'		if(name.match("CPU_ON\[[0-9]*\]"))\n'\
	'			cpu = parseInt(name.slice(7));\n'\
	'		else if(name.match("CPU_OFF\[[0-9]*\]"))\n'\
	'			cpu = parseInt(name.slice(8));\n'\
	'		for (var i = 0; i < dev.length; i++) {\n'\
	'			dname = deviceName(dev[i].title);\n'\
	'			var cname = dev[i].className.slice(dev[i].className.indexOf("thread"));\n'\
	'			if((cpu >= 0 && dname.match("CPU_O[NF]*\\\[*"+cpu+"\\\]")) ||\n'\
	'				(name == dname))\n'\
	'			{\n'\
	'				dev[i].className = "hover "+cname;\n'\
	'			} else {\n'\
	'				dev[i].className = cname;\n'\
	'			}\n'\
	'		}\n'\
	'	}\n'\
	'	function deviceUnhover() {\n'\
	'		var dmesg = document.getElementById("dmesg");\n'\
	'		var dev = dmesg.getElementsByClassName("thread");\n'\
	'		for (var i = 0; i < dev.length; i++) {\n'\
	'			dev[i].className = dev[i].className.slice(dev[i].className.indexOf("thread"));\n'\
	'		}\n'\
	'	}\n'\
	'	function deviceTitle(title, total, cpu) {\n'\
	'		var prefix = "Total";\n'\
	'		if(total.length > 3) {\n'\
	'			prefix = "Average";\n'\
	'			total[1] = (total[1]+total[3])/2;\n'\
	'			total[2] = (total[2]+total[4])/2;\n'\
	'		}\n'\
	'		var devtitle = document.getElementById("devicedetailtitle");\n'\
	'		var name = deviceName(title);\n'\
	'		if(cpu >= 0) name = "CPU"+cpu;\n'\
	'		var driver = "";\n'\
	'		var tS = "<t2>(</t2>";\n'\
	'		var tR = "<t2>)</t2>";\n'\
	'		if(total[1] > 0)\n'\
	'			tS = "<t2>("+prefix+" Suspend:</t2><t0> "+total[1].toFixed(3)+" ms</t0> ";\n'\
	'		if(total[2] > 0)\n'\
	'			tR = " <t2>"+prefix+" Resume:</t2><t0> "+total[2].toFixed(3)+" ms<t2>)</t2></t0>";\n'\
	'		var s = title.indexOf("{");\n'\
	'		var e = title.indexOf("}");\n'\
	'		if((s >= 0) && (e >= 0))\n'\
	'			driver = title.slice(s+1, e) + " <t1>@</t1> ";\n'\
	'		if(total[1] > 0 && total[2] > 0)\n'\
	'			devtitle.innerHTML = "<t0>"+driver+name+"</t0> "+tS+tR;\n'\
	'		else\n'\
	'			devtitle.innerHTML = "<t0>"+title+"</t0>";\n'\
	'		return name;\n'\
	'	}\n'\
	'	function deviceDetail() {\n'\
	'		var devinfo = document.getElementById("devicedetail");\n'\
	'		devinfo.style.display = "block";\n'\
	'		var name = deviceName(this.title);\n'\
	'		var cpu = -1;\n'\
	'		if(name.match("CPU_ON\[[0-9]*\]"))\n'\
	'			cpu = parseInt(name.slice(7));\n'\
	'		else if(name.match("CPU_OFF\[[0-9]*\]"))\n'\
	'			cpu = parseInt(name.slice(8));\n'\
	'		var dmesg = document.getElementById("dmesg");\n'\
	'		var dev = dmesg.getElementsByClassName("thread");\n'\
	'		var idlist = [];\n'\
	'		var pdata = [[]];\n'\
	'		if(document.getElementById("devicedetail1"))\n'\
	'			pdata = [[], []];\n'\
	'		var pd = pdata[0];\n'\
	'		var total = [0.0, 0.0, 0.0];\n'\
	'		for (var i = 0; i < dev.length; i++) {\n'\
	'			dname = deviceName(dev[i].title);\n'\
	'			if((cpu >= 0 && dname.match("CPU_O[NF]*\\\[*"+cpu+"\\\]")) ||\n'\
	'				(name == dname))\n'\
	'			{\n'\
	'				idlist[idlist.length] = dev[i].id;\n'\
	'				var tidx = 1;\n'\
	'				if(dev[i].id[0] == "a") {\n'\
	'					pd = pdata[0];\n'\
	'				} else {\n'\
	'					if(pdata.length == 1) pdata[1] = [];\n'\
	'					if(total.length == 3) total[3]=total[4]=0.0;\n'\
	'					pd = pdata[1];\n'\
	'					tidx = 3;\n'\
	'				}\n'\
	'				var info = dev[i].title.split(" ");\n'\
	'				var pname = info[info.length-1];\n'\
	'				pd[pname] = parseFloat(info[info.length-3].slice(1));\n'\
	'				total[0] += pd[pname];\n'\
	'				if(pname.indexOf("suspend") >= 0)\n'\
	'					total[tidx] += pd[pname];\n'\
	'				else\n'\
	'					total[tidx+1] += pd[pname];\n'\
	'			}\n'\
	'		}\n'\
	'		var devname = deviceTitle(this.title, total, cpu);\n'\
	'		var left = 0.0;\n'\
	'		for (var t = 0; t < pdata.length; t++) {\n'\
	'			pd = pdata[t];\n'\
	'			devinfo = document.getElementById("devicedetail"+t);\n'\
	'			var phases = devinfo.getElementsByClassName("phaselet");\n'\
	'			for (var i = 0; i < phases.length; i++) {\n'\
	'				if(phases[i].id in pd) {\n'\
	'					var w = 100.0*pd[phases[i].id]/total[0];\n'\
	'					var fs = 32;\n'\
	'					if(w < 8) fs = 4*w | 0;\n'\
	'					var fs2 = fs*3/4;\n'\
	'					phases[i].style.width = w+"%";\n'\
	'					phases[i].style.left = left+"%";\n'\
	'					phases[i].title = phases[i].id+" "+pd[phases[i].id]+" ms";\n'\
	'					left += w;\n'\
	'					var time = "<t4 style=\\"font-size:"+fs+"px\\">"+pd[phases[i].id]+" ms<br></t4>";\n'\
	'					var pname = "<t3 style=\\"font-size:"+fs2+"px\\">"+phases[i].id.replace(new RegExp("_", "g"), " ")+"</t3>";\n'\
	'					phases[i].innerHTML = time+pname;\n'\
	'				} else {\n'\
	'					phases[i].style.width = "0%";\n'\
	'					phases[i].style.left = left+"%";\n'\
	'				}\n'\
	'			}\n'\
	'		}\n'\
	'		if(typeof devstats !== \'undefined\')\n'\
	'			callDetail(this.id, this.title);\n'\
	'		var cglist = document.getElementById("callgraphs");\n'\
	'		if(!cglist) return;\n'\
	'		var cg = cglist.getElementsByClassName("atop");\n'\
	'		if(cg.length < 10) return;\n'\
	'		for (var i = 0; i < cg.length; i++) {\n'\
	'			cgid = cg[i].id.split("x")[0]\n'\
	'			if(idlist.indexOf(cgid) >= 0) {\n'\
	'				cg[i].style.display = "block";\n'\
	'			} else {\n'\
	'				cg[i].style.display = "none";\n'\
	'			}\n'\
	'		}\n'\
	'	}\n'\
	'	function callDetail(devid, devtitle) {\n'\
	'		if(!(devid in devstats) || devstats[devid].length < 1)\n'\
	'			return;\n'\
	'		var list = devstats[devid];\n'\
	'		var tmp = devtitle.split(" ");\n'\
	'		var name = tmp[0], phase = tmp[tmp.length-1];\n'\
	'		var dd = document.getElementById(phase);\n'\
	'		var total = parseFloat(tmp[1].slice(1));\n'\
	'		var mlist = [];\n'\
	'		var maxlen = 0;\n'\
	'		var info = []\n'\
	'		for(var i in list) {\n'\
	'			if(list[i][0] == "@") {\n'\
	'				info = list[i].split("|");\n'\
	'				continue;\n'\
	'			}\n'\
	'			var tmp = list[i].split("|");\n'\
	'			var t = parseFloat(tmp[0]), f = tmp[1], c = parseInt(tmp[2]);\n'\
	'			var p = (t*100.0/total).toFixed(2);\n'\
	'			mlist[mlist.length] = [f, c, t.toFixed(2), p+"%"];\n'\
	'			if(f.length > maxlen)\n'\
	'				maxlen = f.length;\n'\
	'		}\n'\
	'		var pad = 5;\n'\
	'		if(mlist.length == 0) pad = 30;\n'\
	'		var html = \'<div style="padding-top:\'+pad+\'px"><t3> <b>\'+name+\':</b>\';\n'\
	'		if(info.length > 2)\n'\
	'			html += " start=<b>"+info[1]+"</b>, end=<b>"+info[2]+"</b>";\n'\
	'		if(info.length > 3)\n'\
	'			html += ", length<i>(w/o overhead)</i>=<b>"+info[3]+" ms</b>";\n'\
	'		if(info.length > 4)\n'\
	'			html += ", return=<b>"+info[4]+"</b>";\n'\
	'		html += "</t3></div>";\n'\
	'		if(mlist.length > 0) {\n'\
	'			html += \'<table class=fstat style="padding-top:\'+(maxlen*5)+\'px;"><tr><th>Function</th>\';\n'\
	'			for(var i in mlist)\n'\
	'				html += "<td class=vt>"+mlist[i][0]+"</td>";\n'\
	'			html += "</tr><tr><th>Calls</th>";\n'\
	'			for(var i in mlist)\n'\
	'				html += "<td>"+mlist[i][1]+"</td>";\n'\
	'			html += "</tr><tr><th>Time(ms)</th>";\n'\
	'			for(var i in mlist)\n'\
	'				html += "<td>"+mlist[i][2]+"</td>";\n'\
	'			html += "</tr><tr><th>Percent</th>";\n'\
	'			for(var i in mlist)\n'\
	'				html += "<td>"+mlist[i][3]+"</td>";\n'\
	'			html += "</tr></table>";\n'\
	'		}\n'\
	'		dd.innerHTML = html;\n'\
	'		var height = (maxlen*5)+100;\n'\
	'		dd.style.height = height+"px";\n'\
	'		document.getElementById("devicedetail").style.height = height+"px";\n'\
	'	}\n'\
	'	function callSelect() {\n'\
	'		var cglist = document.getElementById("callgraphs");\n'\
	'		if(!cglist) return;\n'\
	'		var cg = cglist.getElementsByClassName("atop");\n'\
	'		for (var i = 0; i < cg.length; i++) {\n'\
	'			if(this.id == cg[i].id) {\n'\
	'				cg[i].style.display = "block";\n'\
	'			} else {\n'\
	'				cg[i].style.display = "none";\n'\
	'			}\n'\
	'		}\n'\
	'	}\n'\
	'	function devListWindow(e) {\n'\
	'		var win = window.open();\n'\
	'		var html = "<title>"+e.target.innerHTML+"</title>"+\n'\
	'			"<style type=\\"text/css\\">"+\n'\
	'			"   ul {list-style-type:circle;padding-left:10px;margin-left:10px;}"+\n'\
	'			"</style>"\n'\
	'		var dt = devtable[0];\n'\
	'		if(e.target.id != "devlist1")\n'\
	'			dt = devtable[1];\n'\
	'		win.document.write(html+dt);\n'\
	'	}\n'\
	'	function errWindow() {\n'\
	'		var range = this.id.split("_");\n'\
	'		var idx1 = parseInt(range[0]);\n'\
	'		var idx2 = parseInt(range[1]);\n'\
	'		var win = window.open();\n'\
	'		var log = document.getElementById("dmesglog");\n'\
	'		var title = "<title>dmesg log</title>";\n'\
	'		var text = log.innerHTML.split("\\n");\n'\
	'		var html = "";\n'\
	'		for(var i = 0; i < text.length; i++) {\n'\
	'			if(i == idx1) {\n'\
	'				html += "<e id=target>"+text[i]+"</e>\\n";\n'\
	'			} else if(i > idx1 && i <= idx2) {\n'\
	'				html += "<e>"+text[i]+"</e>\\n";\n'\
	'			} else {\n'\
	'				html += text[i]+"\\n";\n'\
	'			}\n'\
	'		}\n'\
	'		win.document.write("<style>e{color:red}</style>"+title+"<pre>"+html+"</pre>");\n'\
	'		win.location.hash = "#target";\n'\
	'		win.document.close();\n'\
	'	}\n'\
	'	function logWindow(e) {\n'\
	'		var name = e.target.id.slice(4);\n'\
	'		var win = window.open();\n'\
	'		var log = document.getElementById(name+"log");\n'\
	'		var title = "<title>"+document.title.split(" ")[0]+" "+name+" log</title>";\n'\
	'		win.document.write(title+"<pre>"+log.innerHTML+"</pre>");\n'\
	'		win.document.close();\n'\
	'	}\n'\
	'	function onMouseDown(e) {\n'\
	'		dragval[0] = e.clientX;\n'\
	'		dragval[1] = document.getElementById("dmesgzoombox").scrollLeft;\n'\
	'		document.onmousemove = onMouseMove;\n'\
	'	}\n'\
	'	function onMouseMove(e) {\n'\
	'		var zoombox = document.getElementById("dmesgzoombox");\n'\
	'		zoombox.scrollLeft = dragval[1] + dragval[0] - e.clientX;\n'\
	'	}\n'\
	'	function onMouseUp(e) {\n'\
	'		document.onmousemove = null;\n'\
	'	}\n'\
	'	function onKeyPress(e) {\n'\
	'		var c = e.charCode;\n'\
	'		if(c != 42 && c != 43 && c != 45) return;\n'\
	'		var click = document.createEvent("Events");\n'\
	'		click.initEvent("click", true, false);\n'\
	'		if(c == 43)  \n'\
	'			document.getElementById("zoomin").dispatchEvent(click);\n'\
	'		else if(c == 45)\n'\
	'			document.getElementById("zoomout").dispatchEvent(click);\n'\
	'		else if(c == 42)\n'\
	'			document.getElementById("zoomdef").dispatchEvent(click);\n'\
	'	}\n'\
	'	window.addEventListener("resize", function () {zoomTimeline();});\n'\
	'	window.addEventListener("load", function () {\n'\
	'		var dmesg = document.getElementById("dmesg");\n'\
	'		dmesg.style.width = "100%"\n'\
	'		dmesg.onmousedown = onMouseDown;\n'\
	'		document.onmouseup = onMouseUp;\n'\
	'		document.onkeypress = onKeyPress;\n'\
	'		document.getElementById("zoomin").onclick = zoomTimeline;\n'\
	'		document.getElementById("zoomout").onclick = zoomTimeline;\n'\
	'		document.getElementById("zoomdef").onclick = zoomTimeline;\n'\
	'		var list = document.getElementsByClassName("err");\n'\
	'		for (var i = 0; i < list.length; i++)\n'\
	'			list[i].onclick = errWindow;\n'\
	'		var list = document.getElementsByClassName("logbtn");\n'\
	'		for (var i = 0; i < list.length; i++)\n'\
	'			list[i].onclick = logWindow;\n'\
	'		list = document.getElementsByClassName("devlist");\n'\
	'		for (var i = 0; i < list.length; i++)\n'\
	'			list[i].onclick = devListWindow;\n'\
	'		var dev = dmesg.getElementsByClassName("thread");\n'\
	'		for (var i = 0; i < dev.length; i++) {\n'\
	'			dev[i].onclick = deviceDetail;\n'\
	'			dev[i].onmouseover = deviceHover;\n'\
	'			dev[i].onmouseout = deviceUnhover;\n'\
	'		}\n'\
	'		var dev = dmesg.getElementsByClassName("srccall");\n'\
	'		for (var i = 0; i < dev.length; i++)\n'\
	'			dev[i].onclick = callSelect;\n'\
	'		zoomTimeline();\n'\
	'	});\n'\
	'</script>\n'
	hf.write(script_code);

def setRuntimeSuspend(before=True):
	global sysvals
	sv = sysvals
	if sv.rs == 0:
		return
	if before:
		# runtime suspend disable or enable
		if sv.rs > 0:
			sv.rstgt, sv.rsval, sv.rsdir = 'on', 'auto', 'enabled'
		else:
			sv.rstgt, sv.rsval, sv.rsdir = 'auto', 'on', 'disabled'
		print('CONFIGURING RUNTIME SUSPEND...')
		sv.rslist = deviceInfo(sv.rstgt)
		for i in sv.rslist:
			sv.setVal(sv.rsval, i)
		print('runtime suspend %s on all devices (%d changed)' % (sv.rsdir, len(sv.rslist)))
		print('waiting 5 seconds...')
		time.sleep(5)
	else:
		# runtime suspend re-enable or re-disable
		for i in sv.rslist:
			sv.setVal(sv.rstgt, i)
		print('runtime suspend settings restored on %d devices' % len(sv.rslist))

# Function: executeSuspend
# Description:
#	 Execute system suspend through the sysfs interface, then copy the output
#	 dmesg and ftrace files to the test output directory.
def executeSuspend():
	pm = ProcessMonitor()
	tp = sysvals.tpath
	fwdata, bdata = [], []
	battery = True if getBattery() else False
	# run these commands to prepare the system for suspend
	if sysvals.display:
		if sysvals.display > 0:
			print('TURN DISPLAY ON')
			call('xset -d :0.0 dpms force suspend', shell=True)
			call('xset -d :0.0 dpms force on', shell=True)
		else:
			print('TURN DISPLAY OFF')
			call('xset -d :0.0 dpms force suspend', shell=True)
		time.sleep(1)
	if sysvals.sync:
		print('SYNCING FILESYSTEMS')
		call('sync', shell=True)
	# mark the start point in the kernel ring buffer just as we start
	sysvals.initdmesg()
	# start ftrace
	if(sysvals.usecallgraph or sysvals.usetraceevents):
		print('START TRACING')
		sysvals.fsetVal('1', 'tracing_on')
		if sysvals.useprocmon:
			pm.start()
	# execute however many s/r runs requested
	for count in range(1,sysvals.execcount+1):
		# x2delay in between test runs
		if(count > 1 and sysvals.x2delay > 0):
			sysvals.fsetVal('WAIT %d' % sysvals.x2delay, 'trace_marker')
			time.sleep(sysvals.x2delay/1000.0)
			sysvals.fsetVal('WAIT END', 'trace_marker')
		# start message
		if sysvals.testcommand != '':
			print('COMMAND START')
		else:
			if(sysvals.rtcwake):
				print('SUSPEND START')
			else:
				print('SUSPEND START (press a key to resume)')
		bat1 = getBattery() if battery else False
		# set rtcwake
		if(sysvals.rtcwake):
			print('will issue an rtcwake in %d seconds' % sysvals.rtcwaketime)
			sysvals.rtcWakeAlarmOn()
		# start of suspend trace marker
		if(sysvals.usecallgraph or sysvals.usetraceevents):
			sysvals.fsetVal('SUSPEND START', 'trace_marker')
		# predelay delay
		if(count == 1 and sysvals.predelay > 0):
			sysvals.fsetVal('WAIT %d' % sysvals.predelay, 'trace_marker')
			time.sleep(sysvals.predelay/1000.0)
			sysvals.fsetVal('WAIT END', 'trace_marker')
		# initiate suspend or command
		if sysvals.testcommand != '':
			call(sysvals.testcommand+' 2>&1', shell=True);
		else:
			mode = sysvals.suspendmode
			if sysvals.memmode and os.path.exists(sysvals.mempowerfile):
				mode = 'mem'
				pf = open(sysvals.mempowerfile, 'w')
				pf.write(sysvals.memmode)
				pf.close()
			pf = open(sysvals.powerfile, 'w')
			pf.write(mode)
			# execution will pause here
			try:
				pf.close()
			except:
				pass
		if(sysvals.rtcwake):
			sysvals.rtcWakeAlarmOff()
		# postdelay delay
		if(count == sysvals.execcount and sysvals.postdelay > 0):
			sysvals.fsetVal('WAIT %d' % sysvals.postdelay, 'trace_marker')
			time.sleep(sysvals.postdelay/1000.0)
			sysvals.fsetVal('WAIT END', 'trace_marker')
		# return from suspend
		print('RESUME COMPLETE')
		if(sysvals.usecallgraph or sysvals.usetraceevents):
			sysvals.fsetVal('RESUME COMPLETE', 'trace_marker')
		if(sysvals.suspendmode == 'mem' or sysvals.suspendmode == 'command'):
			fwdata.append(getFPDT(False))
		bat2 = getBattery() if battery else False
		if battery and bat1 and bat2:
			bdata.append((bat1, bat2))
	# stop ftrace
	if(sysvals.usecallgraph or sysvals.usetraceevents):
		if sysvals.useprocmon:
			pm.stop()
		sysvals.fsetVal('0', 'tracing_on')
		print('CAPTURING TRACE')
		op = sysvals.writeDatafileHeader(sysvals.ftracefile, fwdata, bdata)
		fp = open(tp+'trace', 'r')
		for line in fp:
			op.write(line)
		op.close()
		sysvals.fsetVal('', 'trace')
		devProps()
	# grab a copy of the dmesg output
	print('CAPTURING DMESG')
	sysvals.getdmesg(fwdata, bdata)

def readFile(file):
	if os.path.islink(file):
		return os.readlink(file).split('/')[-1]
	else:
		return sysvals.getVal(file).strip()

# Function: ms2nice
# Description:
#	 Print out a very concise time string in minutes and seconds
# Output:
#	 The time string, e.g. "1901m16s"
def ms2nice(val):
	val = int(val)
	h = val / 3600000
	m = (val / 60000) % 60
	s = (val / 1000) % 60
	if h > 0:
		return '%d:%02d:%02d' % (h, m, s)
	if m > 0:
		return '%02d:%02d' % (m, s)
	return '%ds' % s

def yesno(val):
	list = {'enabled':'A', 'disabled':'S', 'auto':'E', 'on':'D',
		'active':'A', 'suspended':'S', 'suspending':'S'}
	if val not in list:
		return ' '
	return list[val]

# Function: deviceInfo
# Description:
#	 Detect all the USB hosts and devices currently connected and add
#	 a list of USB device names to sysvals for better timeline readability
def deviceInfo(output=''):
	if not output:
		print('LEGEND')
		print('---------------------------------------------------------------------------------------------')
		print('  A = async/sync PM queue (A/S)               C = runtime active children')
		print('  R = runtime suspend enabled/disabled (E/D)  rACTIVE = runtime active (min/sec)')
		print('  S = runtime status active/suspended (A/S)   rSUSPEND = runtime suspend (min/sec)')
		print('  U = runtime usage count')
		print('---------------------------------------------------------------------------------------------')
		print('DEVICE                     NAME                       A R S U C    rACTIVE   rSUSPEND')
		print('---------------------------------------------------------------------------------------------')

	res = []
	tgtval = 'runtime_status'
	lines = dict()
	for dirname, dirnames, filenames in os.walk('/sys/devices'):
		if(not re.match('.*/power', dirname) or
			'control' not in filenames or
			tgtval not in filenames):
			continue
		name = ''
		dirname = dirname[:-6]
		device = dirname.split('/')[-1]
		power = dict()
		power[tgtval] = readFile('%s/power/%s' % (dirname, tgtval))
		# only list devices which support runtime suspend
		if power[tgtval] not in ['active', 'suspended', 'suspending']:
			continue
		for i in ['product', 'driver', 'subsystem']:
			file = '%s/%s' % (dirname, i)
			if os.path.exists(file):
				name = readFile(file)
				break
		for i in ['async', 'control', 'runtime_status', 'runtime_usage',
			'runtime_active_kids', 'runtime_active_time',
			'runtime_suspended_time']:
			if i in filenames:
				power[i] = readFile('%s/power/%s' % (dirname, i))
		if output:
			if power['control'] == output:
				res.append('%s/power/control' % dirname)
			continue
		lines[dirname] = '%-26s %-26s %1s %1s %1s %1s %1s %10s %10s' % \
			(device[:26], name[:26],
			yesno(power['async']), \
			yesno(power['control']), \
			yesno(power['runtime_status']), \
			power['runtime_usage'], \
			power['runtime_active_kids'], \
			ms2nice(power['runtime_active_time']), \
			ms2nice(power['runtime_suspended_time']))
	for i in sorted(lines):
		print lines[i]
	return res

# Function: devProps
# Description:
#	 Retrieve a list of properties for all devices in the trace log
def devProps(data=0):
	props = dict()

	if data:
		idx = data.index(': ') + 2
		if idx >= len(data):
			return
		devlist = data[idx:].split(';')
		for dev in devlist:
			f = dev.split(',')
			if len(f) < 3:
				continue
			dev = f[0]
			props[dev] = DevProps()
			props[dev].altname = f[1]
			if int(f[2]):
				props[dev].async = True
			else:
				props[dev].async = False
			sysvals.devprops = props
		if sysvals.suspendmode == 'command' and 'testcommandstring' in props:
			sysvals.testcommand = props['testcommandstring'].altname
		return

	if(os.path.exists(sysvals.ftracefile) == False):
		doError('%s does not exist' % sysvals.ftracefile)

	# first get the list of devices we need properties for
	msghead = 'Additional data added by AnalyzeSuspend'
	alreadystamped = False
	tp = TestProps()
	tf = sysvals.openlog(sysvals.ftracefile, 'r')
	for line in tf:
		if msghead in line:
			alreadystamped = True
			continue
		# determine the trace data type (required for further parsing)
		m = re.match(tp.tracertypefmt, line)
		if(m):
			tp.setTracerType(m.group('t'))
			continue
		# parse only valid lines, if this is not one move on
		m = re.match(tp.ftrace_line_fmt, line)
		if(not m or 'device_pm_callback_start' not in line):
			continue
		m = re.match('.*: (?P<drv>.*) (?P<d>.*), parent: *(?P<p>.*), .*', m.group('msg'));
		if(not m):
			continue
		dev = m.group('d')
		if dev not in props:
			props[dev] = DevProps()
	tf.close()

	if not alreadystamped and sysvals.suspendmode == 'command':
		out = '#\n# '+msghead+'\n# Device Properties: '
		out += 'testcommandstring,%s,0;' % (sysvals.testcommand)
		with sysvals.openlog(sysvals.ftracefile, 'a') as fp:
			fp.write(out+'\n')
		sysvals.devprops = props
		return

	# now get the syspath for each of our target devices
	for dirname, dirnames, filenames in os.walk('/sys/devices'):
		if(re.match('.*/power', dirname) and 'async' in filenames):
			dev = dirname.split('/')[-2]
			if dev in props and (not props[dev].syspath or len(dirname) < len(props[dev].syspath)):
				props[dev].syspath = dirname[:-6]

	# now fill in the properties for our target devices
	for dev in props:
		dirname = props[dev].syspath
		if not dirname or not os.path.exists(dirname):
			continue
		with open(dirname+'/power/async') as fp:
			text = fp.read()
			props[dev].async = False
			if 'enabled' in text:
				props[dev].async = True
		fields = os.listdir(dirname)
		if 'product' in fields:
			with open(dirname+'/product') as fp:
				props[dev].altname = fp.read()
		elif 'name' in fields:
			with open(dirname+'/name') as fp:
				props[dev].altname = fp.read()
		elif 'model' in fields:
			with open(dirname+'/model') as fp:
				props[dev].altname = fp.read()
		elif 'description' in fields:
			with open(dirname+'/description') as fp:
				props[dev].altname = fp.read()
		elif 'id' in fields:
			with open(dirname+'/id') as fp:
				props[dev].altname = fp.read()
		elif 'idVendor' in fields and 'idProduct' in fields:
			idv, idp = '', ''
			with open(dirname+'/idVendor') as fp:
				idv = fp.read().strip()
			with open(dirname+'/idProduct') as fp:
				idp = fp.read().strip()
			props[dev].altname = '%s:%s' % (idv, idp)

		if props[dev].altname:
			out = props[dev].altname.strip().replace('\n', ' ')
			out = out.replace(',', ' ')
			out = out.replace(';', ' ')
			props[dev].altname = out

	# and now write the data to the ftrace file
	if not alreadystamped:
		out = '#\n# '+msghead+'\n# Device Properties: '
		for dev in sorted(props):
			out += props[dev].out(dev)
		with sysvals.openlog(sysvals.ftracefile, 'a') as fp:
			fp.write(out+'\n')

	sysvals.devprops = props

# Function: getModes
# Description:
#	 Determine the supported power modes on this system
# Output:
#	 A string list of the available modes
def getModes():
	modes = []
	if(os.path.exists(sysvals.powerfile)):
		fp = open(sysvals.powerfile, 'r')
		modes = string.split(fp.read())
		fp.close()
	if(os.path.exists(sysvals.mempowerfile)):
		deep = False
		fp = open(sysvals.mempowerfile, 'r')
		for m in string.split(fp.read()):
			memmode = m.strip('[]')
			if memmode == 'deep':
				deep = True
			else:
				modes.append('mem-%s' % memmode)
		fp.close()
		if 'mem' in modes and not deep:
			modes.remove('mem')
	return modes

# Function: dmidecode
# Description:
#	 Read the bios tables and pull out system info
# Arguments:
#	 mempath: /dev/mem or custom mem path
#	 fatal: True to exit on error, False to return empty dict
# Output:
#	 A dict object with all available key/values
def dmidecode(mempath, fatal=False):
	out = dict()

	# the list of values to retrieve, with hardcoded (type, idx)
	info = {
		'bios-vendor': (0, 4),
		'bios-version': (0, 5),
		'bios-release-date': (0, 8),
		'system-manufacturer': (1, 4),
		'system-product-name': (1, 5),
		'system-version': (1, 6),
		'system-serial-number': (1, 7),
		'baseboard-manufacturer': (2, 4),
		'baseboard-product-name': (2, 5),
		'baseboard-version': (2, 6),
		'baseboard-serial-number': (2, 7),
		'chassis-manufacturer': (3, 4),
		'chassis-type': (3, 5),
		'chassis-version': (3, 6),
		'chassis-serial-number': (3, 7),
		'processor-manufacturer': (4, 7),
		'processor-version': (4, 16),
	}
	if(not os.path.exists(mempath)):
		if(fatal):
			doError('file does not exist: %s' % mempath)
		return out
	if(not os.access(mempath, os.R_OK)):
		if(fatal):
			doError('file is not readable: %s' % mempath)
		return out

	# by default use legacy scan, but try to use EFI first
	memaddr = 0xf0000
	memsize = 0x10000
	for ep in ['/sys/firmware/efi/systab', '/proc/efi/systab']:
		if not os.path.exists(ep) or not os.access(ep, os.R_OK):
			continue
		fp = open(ep, 'r')
		buf = fp.read()
		fp.close()
		i = buf.find('SMBIOS=')
		if i >= 0:
			try:
				memaddr = int(buf[i+7:], 16)
				memsize = 0x20
			except:
				continue

	# read in the memory for scanning
	fp = open(mempath, 'rb')
	try:
		fp.seek(memaddr)
		buf = fp.read(memsize)
	except:
		if(fatal):
			doError('DMI table is unreachable, sorry')
		else:
			return out
	fp.close()

	# search for either an SM table or DMI table
	i = base = length = num = 0
	while(i < memsize):
		if buf[i:i+4] == '_SM_' and i < memsize - 16:
			length = struct.unpack('H', buf[i+22:i+24])[0]
			base, num = struct.unpack('IH', buf[i+24:i+30])
			break
		elif buf[i:i+5] == '_DMI_':
			length = struct.unpack('H', buf[i+6:i+8])[0]
			base, num = struct.unpack('IH', buf[i+8:i+14])
			break
		i += 16
	if base == 0 and length == 0 and num == 0:
		if(fatal):
			doError('Neither SMBIOS nor DMI were found')
		else:
			return out

	# read in the SM or DMI table
	fp = open(mempath, 'rb')
	try:
		fp.seek(base)
		buf = fp.read(length)
	except:
		if(fatal):
			doError('DMI table is unreachable, sorry')
		else:
			return out
	fp.close()

	# scan the table for the values we want
	count = i = 0
	while(count < num and i <= len(buf) - 4):
		type, size, handle = struct.unpack('BBH', buf[i:i+4])
		n = i + size
		while n < len(buf) - 1:
			if 0 == struct.unpack('H', buf[n:n+2])[0]:
				break
			n += 1
		data = buf[i+size:n+2].split('\0')
		for name in info:
			itype, idxadr = info[name]
			if itype == type:
				idx = struct.unpack('B', buf[i+idxadr])[0]
				if idx > 0 and idx < len(data) - 1:
					s = data[idx-1].strip()
					if s and s.lower() != 'to be filled by o.e.m.':
						out[name] = data[idx-1]
		i = n + 2
		count += 1
	return out

def getBattery():
	p, charge, bat = '/sys/class/power_supply', 0, {}
	if not os.path.exists(p):
		return False
	for d in os.listdir(p):
		type = sysvals.getVal(os.path.join(p, d, 'type')).strip().lower()
		if type != 'battery':
			continue
		for v in ['status', 'energy_now', 'capacity_now']:
			bat[v] = sysvals.getVal(os.path.join(p, d, v)).strip().lower()
		break
	if 'status' not in bat:
		return False
	ac = False if 'discharging' in bat['status'] else True
	for v in ['energy_now', 'capacity_now']:
		if v in bat and bat[v]:
			charge = int(bat[v])
	return (ac, charge)

# Function: getFPDT
# Description:
#	 Read the acpi bios tables and pull out FPDT, the firmware data
# Arguments:
#	 output: True to output the info to stdout, False otherwise
def getFPDT(output):
	rectype = {}
	rectype[0] = 'Firmware Basic Boot Performance Record'
	rectype[1] = 'S3 Performance Table Record'
	prectype = {}
	prectype[0] = 'Basic S3 Resume Performance Record'
	prectype[1] = 'Basic S3 Suspend Performance Record'

	sysvals.rootCheck(True)
	if(not os.path.exists(sysvals.fpdtpath)):
		if(output):
			doError('file does not exist: %s' % sysvals.fpdtpath)
		return False
	if(not os.access(sysvals.fpdtpath, os.R_OK)):
		if(output):
			doError('file is not readable: %s' % sysvals.fpdtpath)
		return False
	if(not os.path.exists(sysvals.mempath)):
		if(output):
			doError('file does not exist: %s' % sysvals.mempath)
		return False
	if(not os.access(sysvals.mempath, os.R_OK)):
		if(output):
			doError('file is not readable: %s' % sysvals.mempath)
		return False

	fp = open(sysvals.fpdtpath, 'rb')
	buf = fp.read()
	fp.close()

	if(len(buf) < 36):
		if(output):
			doError('Invalid FPDT table data, should '+\
				'be at least 36 bytes')
		return False

	table = struct.unpack('4sIBB6s8sI4sI', buf[0:36])
	if(output):
		print('')
		print('Firmware Performance Data Table (%s)' % table[0])
		print('                  Signature : %s' % table[0])
		print('               Table Length : %u' % table[1])
		print('                   Revision : %u' % table[2])
		print('                   Checksum : 0x%x' % table[3])
		print('                     OEM ID : %s' % table[4])
		print('               OEM Table ID : %s' % table[5])
		print('               OEM Revision : %u' % table[6])
		print('                 Creator ID : %s' % table[7])
		print('           Creator Revision : 0x%x' % table[8])
		print('')

	if(table[0] != 'FPDT'):
		if(output):
			doError('Invalid FPDT table')
		return False
	if(len(buf) <= 36):
		return False
	i = 0
	fwData = [0, 0]
	records = buf[36:]
	fp = open(sysvals.mempath, 'rb')
	while(i < len(records)):
		header = struct.unpack('HBB', records[i:i+4])
		if(header[0] not in rectype):
			i += header[1]
			continue
		if(header[1] != 16):
			i += header[1]
			continue
		addr = struct.unpack('Q', records[i+8:i+16])[0]
		try:
			fp.seek(addr)
			first = fp.read(8)
		except:
			if(output):
				print('Bad address 0x%x in %s' % (addr, sysvals.mempath))
			return [0, 0]
		rechead = struct.unpack('4sI', first)
		recdata = fp.read(rechead[1]-8)
		if(rechead[0] == 'FBPT'):
			record = struct.unpack('HBBIQQQQQ', recdata)
			if(output):
				print('%s (%s)' % (rectype[header[0]], rechead[0]))
				print('                  Reset END : %u ns' % record[4])
				print('  OS Loader LoadImage Start : %u ns' % record[5])
				print(' OS Loader StartImage Start : %u ns' % record[6])
				print('     ExitBootServices Entry : %u ns' % record[7])
				print('      ExitBootServices Exit : %u ns' % record[8])
		elif(rechead[0] == 'S3PT'):
			if(output):
				print('%s (%s)' % (rectype[header[0]], rechead[0]))
			j = 0
			while(j < len(recdata)):
				prechead = struct.unpack('HBB', recdata[j:j+4])
				if(prechead[0] not in prectype):
					continue
				if(prechead[0] == 0):
					record = struct.unpack('IIQQ', recdata[j:j+prechead[1]])
					fwData[1] = record[2]
					if(output):
						print('    %s' % prectype[prechead[0]])
						print('               Resume Count : %u' % \
							record[1])
						print('                 FullResume : %u ns' % \
							record[2])
						print('              AverageResume : %u ns' % \
							record[3])
				elif(prechead[0] == 1):
					record = struct.unpack('QQ', recdata[j+4:j+prechead[1]])
					fwData[0] = record[1] - record[0]
					if(output):
						print('    %s' % prectype[prechead[0]])
						print('               SuspendStart : %u ns' % \
							record[0])
						print('                 SuspendEnd : %u ns' % \
							record[1])
						print('                SuspendTime : %u ns' % \
							fwData[0])
				j += prechead[1]
		if(output):
			print('')
		i += header[1]
	fp.close()
	return fwData

# Function: submitAttachment
# Description:
#	 Attach a file to an existing bug submission
def submitAttachment(db, stamp, bugid, file, title=''):
	if 'user' in db and 'pass' in db:
		url = '%s/bug/%d/attachment?login=%s&password=%s' % \
			(stamp['url'], bugid, db['user'], db['pass'])
	else:
		url = '%s/bug/%d/attachment?api_key=%s' % \
			(stamp['url'], bugid, db['apikey'])
	head = {'content-type': 'application/json'}
	content = open(file, 'r').read()
	ext = file.split('.')[-1]
	if ext == 'gz':
		ctype = 'application/gzip'
	else:
		ctype = 'text/html'
	if not title:
		if ext == 'html':
			title = 'HTML Timeline'
		else:
			title = file
	data = json.JSONEncoder().encode({
		'ids' : [ bugid ],
		'is_patch' : False,
		'is_markdown' : False,
		'summary' : title,
		'content_type' : ctype,
		'data' : base64.b64encode(content),
		'file_name' : file,
		'obsoletes' : [],
		'is_private' : False,
	})
	res = requests.post(url, data=data, headers=head)
	res.raise_for_status()
	attachurl = stamp['url'].replace('rest.cgi', 'attachment.cgi')
	link = '%s?id=%s' % (attachurl, res.json()['ids'][0])
	return link

# Function: submitTimeline
# Description:
#	 Submit an html timeline to bugzilla
def submitTimeline(db, stamp, attach):
	if 'plat' not in stamp or 'man' not in stamp or 'cpu' not in stamp or \
		not stamp['man'] or not stamp['plat'] or not stamp['cpu']:
		doError('This timeline cannot be submitted, missing hardware info')
	if 'apikey' not in db and ('user' not in db or 'pass' not in db):
		doError('missing login info and api key for submission')

	# set up the url and base variables
	showurl = stamp['url'].replace('rest.cgi', 'show_bug.cgi')
	os.environ['no_proxy'] = stamp['url'].split('/')[2]
	if 'user' in db and 'pass' in db:
		url = '%s/bug?login=%s&password=%s' % \
			(stamp['url'], db['user'], db['pass'])
	else:
		url = '%s/bug?api_key=%s' % (stamp['url'], db['apikey'])
	component = stamp['app']
	if 'extra' in db:
		component += '-debug'
	dt = datetime.strptime(stamp['time'], '%B %d %Y, %I:%M:%S %p')
	cf_datetime = dt.strftime('%Y-%m-%d %H:%M:%S')
	if 'desc' in db:
		summary = db['desc']
	else:
		summary = '%s %s timeline' % (stamp['plat'], stamp['mode'])
	head = {'content-type': 'application/json'}
	rawdata = {
		'product' : 'pm-graph',
		'component' : component,
		'version' : '4.7',
		'op_sys' : 'Linux',
		'rep_platform' : 'PC',
		'cf_platform' : stamp['plat'],
		'cf_cpu' : stamp['cpu'],
		'cf_manufacturer' : stamp['man'],
		'cf_kernel' : stamp['kernel'],
		'cf_power_mode' : stamp['mode'],
		'cf_datetime' : cf_datetime,
		'severity' : 'enhancement',
		'priority' : 'normal'
	}
	if 'bios' in stamp:
		rawdata['cf_bios'] = stamp['bios']
	if 'numcpu' in stamp:
		rawdata['cf_cpu_count'] = stamp['numcpu']
	if 'memsz' in stamp:
		rawdata['cf_mem_total'] = stamp['memsz']
	for tprop in ['suspend', 'resume', 'boot']:
		if tprop in stamp:
			rawdata['cf_'+tprop+'_time'] = int(round(stamp[tprop]*1000))
	if 'offenders' in stamp:
		list = stamp['offenders']
		if len(list) > 0:
			rawdata['cf_worst_perf1'] = list[0]
		if len(list) > 1:
			rawdata['cf_worst_perf2'] = list[1]
		if len(list) > 2:
			rawdata['cf_worst_perf3'] = list[2]
	if 'extra' in db:
		rawdata['cf_debug'] = db['extra']

	# check for duplicate submission
	res = requests.get('%s&%s' % (url, urllib.urlencode(rawdata)))
	res.raise_for_status()
	bugs = res.json()['bugs']
	if len(bugs) > 0:
		print('ALREADY SUBMITTED: %s?id=%s' % (showurl, bugs[0]['id']))
		out = {
			'bugid': bugs[0]['id'],
			'bugurl': '%s?id=%s' % (showurl, bugs[0]['id'])
		}
		return out

	# create a new bug
	rawdata['summary'] = summary
	if 'extra' not in db:
		print('SUBMITTING TIMELINE')
	elif db['extra'] == 'bugreport':
		print('SUBMITTING BUG REPORT')
	else:
		print('SUBMITTING %s TIMELINE' % db['extra'].upper())
	data = json.JSONEncoder().encode(rawdata)
	res = requests.post(url, data=data, headers=head)
	res.raise_for_status()
	bugid = res.json()['id']

	# attach the files to the bug
	out = {
		'bugid': bugid,
		'bugurl': '%s?id=%s' % (showurl, bugid)
	}
	for file in attach:
		out[file] = submitAttachment(db, stamp, bugid, file)
	print('SUCCESS: %s' % out['bugurl'])
	return out

def submitMultiTimeline(htmlsummary, submit):
	files = []
	stamps = dict()
	devlist = dict()
	mstamp = False
	msubmit = False
	fp = open(htmlsummary, 'r')
	for line in fp:
		m = re.match('.*<a href="(?P<f>.*)">html</a>', line)
		if not m:
			continue
		log = os.path.join('.', m.group('f').replace('.html', ''))
		sysvals.dmesgfile = ''
		for l in [log+'_dmesg.txt', log+'_dmesg.txt.gz']:
			if os.path.exists(l) and os.access(l, os.R_OK):
				sysvals.dmesgfile = l
		if not sysvals.dmesgfile:
			doError('file not found - %s_dmesg.txt' % log)
		sysvals.ftracefile = ''
		for l in [log+'_ftrace.txt', log+'_ftrace.txt.gz']:
			if os.path.exists(l) and os.access(l, os.R_OK):
				sysvals.ftracefile = l
		if not sysvals.ftracefile:
			doError('file not found - %s_ftrace.txt' % log)
		submit, stamp, htmlfile = rerunTest(submit)
		files.append(htmlfile)
		stamps[htmlfile] = stamp.copy()
		if not mstamp:
			mstamp = stamp.copy()
			msubmit = submit.copy()
		else:
			for key in ['suspend', 'resume']:
				if stamp[key] > mstamp[key]:
					mstamp[key] = stamp[key]
		if 'extra' in submit:
			if submit['extra'] == 'callgraph':
				for file in files:
					os.remove(file)
				doError('callgraph data found, please submit these one at a time')
			else:
				msubmit['extra'] = submit['extra']
		if 'offenders' in stamp:
			for val in stamp['offenders']:
				v = val.rsplit('(', 1)
				d, t = v[0].strip(), int(v[1].split(' ')[0])
				if d not in devlist or t > devlist[d]:
					devlist[d] = t
	fp.close()
	if len(files) < 1:
		return
	if 'desc' not in msubmit:
		msubmit['desc'] = '%s %s timeline (x%d)' % \
			(mstamp['plat'], mstamp['mode'], len(files))
	mstamp['offenders'] = []
	for d in sorted(devlist, key=devlist.get, reverse=True):
		mstamp['offenders'].append('%s (%.0f ms)' % (d, devlist[d]))
		if len(mstamp['offenders']) >= 10:
			break
	out = submitTimeline(msubmit, mstamp, files)
	valid = True
	for file in files:
		if file in out:
			stamps[file]['url'] = stamps[file]['bugurl'] = out[file]
		else:
			valid = False
		os.remove(file)
	if not valid:
		doError('timeline already submitted')
		return
	print('ATTACHING SUMMARY')
	file = datetime.now().strftime('/tmp/summary-%y%m%d-%H%M%S-%f-')
	file += '%d.html' % os.getpid()
	createHTMLSummarySimple(stamps.values(), file, mstamp['plat'])
	submitAttachment(msubmit, mstamp, out['bugid'], file, 'Summary')
	for i in range(len(files)):
		sysvals.outputResult(stamps[files[i]], i+1)
	print('DONE')

# Description:
#	 Verify that the requested command and options will work, and
#	 print the results to the terminal
# Output:
#	 True if the test will work, False if not
def statusCheck(probecheck=False):
	status = True

	print('Checking this system (%s)...' % platform.node())

	# check we have root access
	res = sysvals.colorText('NO (No features of this tool will work!)')
	if(sysvals.rootCheck(False)):
		res = 'YES'
	print('    have root access: %s' % res)
	if(res != 'YES'):
		print('    Try running this script with sudo')
		return False

	# check sysfs is mounted
	res = sysvals.colorText('NO (No features of this tool will work!)')
	if(os.path.exists(sysvals.powerfile)):
		res = 'YES'
	print('    is sysfs mounted: %s' % res)
	if(res != 'YES'):
		return False

	# check target mode is a valid mode
	if sysvals.suspendmode != 'command':
		res = sysvals.colorText('NO')
		modes = getModes()
		if(sysvals.suspendmode in modes):
			res = 'YES'
		else:
			status = False
		print('    is "%s" a valid power mode: %s' % (sysvals.suspendmode, res))
		if(res == 'NO'):
			print('      valid power modes are: %s' % modes)
			print('      please choose one with -m')

	# check if ftrace is available
	res = sysvals.colorText('NO')
	ftgood = sysvals.verifyFtrace()
	if(ftgood):
		res = 'YES'
	elif(sysvals.usecallgraph):
		status = False
	print('    is ftrace supported: %s' % res)

	# check if kprobes are available
	res = sysvals.colorText('NO')
	sysvals.usekprobes = sysvals.verifyKprobes()
	if(sysvals.usekprobes):
		res = 'YES'
	else:
		sysvals.usedevsrc = False
	print('    are kprobes supported: %s' % res)

	# what data source are we using
	res = 'DMESG'
	if(ftgood):
		sysvals.usetraceevents = True
		for e in sysvals.traceevents:
			if not os.path.exists(sysvals.epath+e):
				sysvals.usetraceevents = False
		if(sysvals.usetraceevents):
			res = 'FTRACE (all trace events found)'
	print('    timeline data source: %s' % res)

	# check if rtcwake
	res = sysvals.colorText('NO')
	if(sysvals.rtcpath != ''):
		res = 'YES'
	elif(sysvals.rtcwake):
		status = False
	print('    is rtcwake supported: %s' % res)

	if not probecheck:
		return status

	# verify kprobes
	if sysvals.usekprobes:
		for name in sysvals.tracefuncs:
			sysvals.defaultKprobe(name, sysvals.tracefuncs[name])
		if sysvals.usedevsrc:
			for name in sysvals.dev_tracefuncs:
				sysvals.defaultKprobe(name, sysvals.dev_tracefuncs[name])
		sysvals.addKprobes(True)

	return status

# Function: doError
# Description:
#	 generic error function for catastrphic failures
# Arguments:
#	 msg: the error message to print
#	 help: True if printHelp should be called after, False otherwise
def doError(msg, help=False):
	if(help == True):
		printHelp()
	print('ERROR: %s\n') % msg
	sysvals.outputResult({'error':msg})
	sys.exit(1)

# Function: getArgInt
# Description:
#	 pull out an integer argument from the command line with checks
def getArgInt(name, args, min, max, main=True):
	if main:
		try:
			arg = args.next()
		except:
			doError(name+': no argument supplied', True)
	else:
		arg = args
	try:
		val = int(arg)
	except:
		doError(name+': non-integer value given', True)
	if(val < min or val > max):
		doError(name+': value should be between %d and %d' % (min, max), True)
	return val

# Function: getArgFloat
# Description:
#	 pull out a float argument from the command line with checks
def getArgFloat(name, args, min, max, main=True):
	if main:
		try:
			arg = args.next()
		except:
			doError(name+': no argument supplied', True)
	else:
		arg = args
	try:
		val = float(arg)
	except:
		doError(name+': non-numerical value given', True)
	if(val < min or val > max):
		doError(name+': value should be between %f and %f' % (min, max), True)
	return val

def processData(live=False):
	print('PROCESSING DATA')
	error = ''
	if(sysvals.usetraceevents):
		testruns, error = parseTraceLog(live)
		if sysvals.dmesgfile:
			for data in testruns:
				data.extractErrorInfo()
	else:
		testruns = loadKernelLog()
		for data in testruns:
			parseKernelLog(data)
		if(sysvals.ftracefile and (sysvals.usecallgraph or sysvals.usetraceevents)):
			appendIncompleteTraceLog(testruns)
	sysvals.vprint('Command:\n    %s' % sysvals.cmdline)
	for data in testruns:
		if data.battery:
			a1, c1, a2, c2 = data.battery
			s = 'Battery:\n    Before - AC: %s, Charge: %d\n     After - AC: %s, Charge: %d' % \
				(a1, int(c1), a2, int(c2))
			sysvals.vprint(s)
		data.printDetails()
	if sysvals.cgdump:
		for data in testruns:
			data.debugPrint()
		sys.exit(0)
	if len(testruns) < 1:
		return (testruns, {'error': 'timeline generation failed'})
	sysvals.vprint('Creating the html timeline (%s)...' % sysvals.htmlfile)
	createHTML(testruns, error)
	print('DONE')
	data = testruns[0]
	stamp = data.stamp
	stamp['suspend'], stamp['resume'] = data.getTimeValues()
	if data.fwValid:
		stamp['fwsuspend'], stamp['fwresume'] = data.fwSuspend, data.fwResume
	if error:
		stamp['error'] = error
	return (testruns, stamp)

def bugReport(sv, submit):
	tp = TestProps()
	files = []
	# extract the stamp and sysinfo
	for file in [sv.dmesgfile, sv.ftracefile]:
		if not file:
			continue
		files.append(file)
		fp = sysvals.openlog(file, 'r')
		for line in fp:
			if tp.stamp and tp.sysinfo:
				break
			line = line.strip()
			if re.match(tp.stampfmt, line):
				tp.stamp = line
			elif re.match(tp.sysinfofmt, line):
				tp.sysinfo = line
		fp.close()
	if not tp.stamp or not tp.sysinfo:
		doError('unrecognized log file format, header is missing')
	data = Data(0)
	tp.parseStamp(data, sv)
	# gzip the logs if possible
	gz = sv.getExec('gzip')
	attach = []
	for file in files:
		if file[-3:] == '.gz':
			attach.append(file)
			continue
		gf = file+'.gz'
		if gz and call('%s -f -k -9 %s' % (gz, file), shell=True) == 0 \
			and os.path.exists(gf):
			file = gf
		attach.append(file)
	submitTimeline(submit, data.stamp, attach)
	# remove gz files
	for file in attach:
		if file[-3:] == '.gz' and os.path.exists(file):
			os.remove(file)

# Function: rerunTest
# Description:
#	 generate an output from an existing set of ftrace/dmesg logs
def rerunTest(submit=False):
	if sysvals.ftracefile:
		doesTraceLogHaveTraceEvents()
	if not sysvals.dmesgfile and not sysvals.usetraceevents:
		doError('recreating this html output requires a dmesg file')
	if submit:
		sysvals.submitOptions()
		sysvals.htmlfile = datetime.now().strftime('/tmp/timeline-%y%m%d-%H%M%S-%f-')
		sysvals.htmlfile += '%d.html' % os.getpid()
	else:
		sysvals.setOutputFile()
	if os.path.exists(sysvals.htmlfile):
		if not os.path.isfile(sysvals.htmlfile):
			doError('a directory already exists with this name: %s' % sysvals.htmlfile)
		elif not os.access(sysvals.htmlfile, os.W_OK):
			doError('missing permission to write to %s' % sysvals.htmlfile)
	testruns, stamp = processData(False)
	if submit:
		stamp['offenders'] = testruns[0].worstOffenders(sysvals.devprops)
		if sysvals.extra:
			submit['extra'] = sysvals.extra
	sysvals.logmsg = ''
	return (submit, stamp, sysvals.htmlfile)

# Function: runTest
# Description:
#	 execute a suspend/resume, gather the logs, and generate the output
def runTest(n=0):
	# prepare for the test
	sysvals.initFtrace()
	sysvals.initTestOutput('suspend')

	# execute the test
	executeSuspend()
	sysvals.cleanupFtrace()
	if sysvals.skiphtml:
		sysvals.sudouser(sysvals.testdir)
		return
	testruns, stamp = processData(True)
	for data in testruns:
		del data
	sysvals.sudouser(sysvals.testdir)
	sysvals.outputResult(stamp, n)
	if 'error' in stamp:
		return 2
	return 0

def find_in_html(html, start, end, firstonly=True):
	n, out = 0, []
	while n < len(html):
		m = re.search(start, html[n:])
		if not m:
			break
		i = m.end()
		m = re.search(end, html[n+i:])
		if not m:
			break
		j = m.start()
		str = html[n+i:n+i+j]
		if end == 'ms':
			num = re.search(r'[-+]?\d*\.\d+|\d+', str)
			str = num.group() if num else 'NaN'
		if firstonly:
			return str
		out.append(str)
		n += i+j
	if firstonly:
		return ''
	return out

def data_from_html(file, outpath, devlist=False):
	html = open(file, 'r').read()
	suspend = find_in_html(html, 'Kernel Suspend', 'ms')
	resume = find_in_html(html, 'Kernel Resume', 'ms')
	line = find_in_html(html, '<div class="stamp">', '</div>')
	stmp = line.split()
	if not suspend or not resume or len(stmp) != 8:
		return False
	try:
		dt = datetime.strptime(' '.join(stmp[3:]), '%B %d %Y, %I:%M:%S %p')
	except:
		return False
	tstr = dt.strftime('%Y/%m/%d %H:%M:%S')
	error = find_in_html(html, '<table class="testfail"><tr><td>', '</td>')
	result = 'fail' if error else 'pass'
	ilist = []
	e = find_in_html(html, 'class="err"[\w=":;\.%\- ]*>', '&rarr;</div>', False)
	for i in list(set(e)):
		ilist.append('%sx%d' % (i, e.count(i)) if e.count(i) > 1 else i)
	devices = dict()
	for line in html.split('\n'):
		m = re.match(' *<div id=\"[a,0-9]*\" *title=\"(?P<title>.*)\" class=\"thread.*', line)
		if not m or 'thread kth' in line or 'thread sec' in line:
			continue
		m = re.match('(?P<n>.*) \((?P<t>[0-9,\.]*) ms\) (?P<p>.*)', m.group('title'))
		if not m:
			continue
		name, time, phase = m.group('n'), m.group('t'), m.group('p')
		if ' async' in name or ' sync' in name:
			name = ' '.join(name.split(' ')[:-1])
		devices[name+' '+phase] = float(time)
	wd, wdt = '', 0
	if len(devices.keys()) > 0:
		n = sorted(devices, key=devices.get, reverse=True)[0]
		wd, wdt = n, devices[n]
	data = {
		'mode': stmp[2],
		'host': stmp[0],
		'kernel': stmp[1],
		'time': tstr,
		'result': result,
		'issues': ' '.join(ilist),
		'suspend': suspend,
		'resume': resume,
		'worst': wd,
		'worsttime': wdt,
		'url': os.path.relpath(file, outpath),
	}
	if devlist:
		data['devlist'] = devices
	return data

# Function: runSummary
# Description:
#	 create a summary of tests in a sub-directory
def runSummary(subdir, local=True, genhtml=False):
	inpath = os.path.abspath(subdir)
	outpath = os.path.abspath('.') if local else inpath
	print('Generating a summary of folder "%s"' % inpath)
	if genhtml:
		for dirname, dirnames, filenames in os.walk(subdir):
			sysvals.dmesgfile = sysvals.ftracefile = sysvals.htmlfile = ''
			for filename in filenames:
				if(re.match('.*_dmesg.txt', filename)):
					sysvals.dmesgfile = os.path.join(dirname, filename)
				elif(re.match('.*_ftrace.txt', filename)):
					sysvals.ftracefile = os.path.join(dirname, filename)
			sysvals.setOutputFile()
			if sysvals.ftracefile and sysvals.htmlfile and \
				not os.path.exists(sysvals.htmlfile):
				print('FTRACE: %s' % sysvals.ftracefile)
				if sysvals.dmesgfile:
					print('DMESG : %s' % sysvals.dmesgfile)
				rerunTest()
	testruns = []
	for dirname, dirnames, filenames in os.walk(subdir):
		for filename in filenames:
			if(not re.match('.*.html', filename)):
				continue
			data = data_from_html(os.path.join(dirname, filename), outpath)
			if(not data):
				continue
			testruns.append(data)
	outfile = os.path.join(outpath, 'summary.html')
	print('Summary file: %s' % outfile)
	createHTMLSummarySimple(testruns, outfile, inpath)

# Function: checkArgBool
# Description:
#	 check if a boolean string value is true or false
def checkArgBool(name, value):
	if value in switchvalues:
		if value in switchoff:
			return False
		return True
	doError('invalid boolean --> (%s: %s), use "true/false" or "1/0"' % (name, value), True)
	return False

# Function: configFromFile
# Description:
#	 Configure the script via the info in a config file
def configFromFile(file):
	Config = ConfigParser.ConfigParser()

	Config.read(file)
	sections = Config.sections()
	overridekprobes = False
	overridedevkprobes = False
	if 'Settings' in sections:
		for opt in Config.options('Settings'):
			value = Config.get('Settings', opt).lower()
			option = opt.lower()
			if(option == 'verbose'):
				sysvals.verbose = checkArgBool(option, value)
			elif(option == 'addlogs'):
				sysvals.dmesglog = sysvals.ftracelog = checkArgBool(option, value)
			elif(option == 'dev'):
				sysvals.usedevsrc = checkArgBool(option, value)
			elif(option == 'proc'):
				sysvals.useprocmon = checkArgBool(option, value)
			elif(option == 'x2'):
				if checkArgBool(option, value):
					sysvals.execcount = 2
			elif(option == 'callgraph'):
				sysvals.usecallgraph = checkArgBool(option, value)
			elif(option == 'override-timeline-functions'):
				overridekprobes = checkArgBool(option, value)
			elif(option == 'override-dev-timeline-functions'):
				overridedevkprobes = checkArgBool(option, value)
			elif(option == 'skiphtml'):
				sysvals.skiphtml = checkArgBool(option, value)
			elif(option == 'sync'):
				sysvals.sync = checkArgBool(option, value)
			elif(option == 'rs' or option == 'runtimesuspend'):
				if value in switchvalues:
					if value in switchoff:
						sysvals.rs = -1
					else:
						sysvals.rs = 1
				else:
					doError('invalid value --> (%s: %s), use "enable/disable"' % (option, value), True)
			elif(option == 'display'):
				if value in switchvalues:
					if value in switchoff:
						sysvals.display = -1
					else:
						sysvals.display = 1
				else:
					doError('invalid value --> (%s: %s), use "on/off"' % (option, value), True)
			elif(option == 'gzip'):
				sysvals.gzip = checkArgBool(option, value)
			elif(option == 'cgfilter'):
				sysvals.setCallgraphFilter(value)
			elif(option == 'cgskip'):
				if value in switchoff:
					sysvals.cgskip = ''
				else:
					sysvals.cgskip = sysvals.configFile(val)
					if(not sysvals.cgskip):
						doError('%s does not exist' % sysvals.cgskip)
			elif(option == 'cgtest'):
				sysvals.cgtest = getArgInt('cgtest', value, 0, 1, False)
			elif(option == 'cgphase'):
				d = Data(0)
				if value not in d.sortedPhases():
					doError('invalid phase --> (%s: %s), valid phases are %s'\
						% (option, value, d.sortedPhases()), True)
				sysvals.cgphase = value
			elif(option == 'fadd'):
				file = sysvals.configFile(value)
				if(not file):
					doError('%s does not exist' % value)
				sysvals.addFtraceFilterFunctions(file)
			elif(option == 'result'):
				sysvals.result = value
			elif(option == 'multi'):
				nums = value.split()
				if len(nums) != 2:
					doError('multi requires 2 integers (exec_count and delay)', True)
				sysvals.multitest['run'] = True
				sysvals.multitest['count'] = getArgInt('multi: n d (exec count)', nums[0], 2, 1000000, False)
				sysvals.multitest['delay'] = getArgInt('multi: n d (delay between tests)', nums[1], 0, 3600, False)
			elif(option == 'devicefilter'):
				sysvals.setDeviceFilter(value)
			elif(option == 'expandcg'):
				sysvals.cgexp = checkArgBool(option, value)
			elif(option == 'srgap'):
				if checkArgBool(option, value):
					sysvals.srgap = 5
			elif(option == 'mode'):
				sysvals.suspendmode = value
			elif(option == 'command' or option == 'cmd'):
				sysvals.testcommand = value
			elif(option == 'x2delay'):
				sysvals.x2delay = getArgInt('x2delay', value, 0, 60000, False)
			elif(option == 'predelay'):
				sysvals.predelay = getArgInt('predelay', value, 0, 60000, False)
			elif(option == 'postdelay'):
				sysvals.postdelay = getArgInt('postdelay', value, 0, 60000, False)
			elif(option == 'maxdepth'):
				sysvals.max_graph_depth = getArgInt('maxdepth', value, 0, 1000, False)
			elif(option == 'rtcwake'):
				if value in switchoff:
					sysvals.rtcwake = False
				else:
					sysvals.rtcwake = True
					sysvals.rtcwaketime = getArgInt('rtcwake', value, 0, 3600, False)
			elif(option == 'timeprec'):
				sysvals.setPrecision(getArgInt('timeprec', value, 0, 6, False))
			elif(option == 'mindev'):
				sysvals.mindevlen = getArgFloat('mindev', value, 0.0, 10000.0, False)
			elif(option == 'callloop-maxgap'):
				sysvals.callloopmaxgap = getArgFloat('callloop-maxgap', value, 0.0, 1.0, False)
			elif(option == 'callloop-maxlen'):
				sysvals.callloopmaxgap = getArgFloat('callloop-maxlen', value, 0.0, 1.0, False)
			elif(option == 'mincg'):
				sysvals.mincglen = getArgFloat('mincg', value, 0.0, 10000.0, False)
			elif(option == 'bufsize'):
				sysvals.bufsize = getArgInt('bufsize', value, 1, 1024*1024*8, False)
			elif(option == 'output-dir'):
				sysvals.outdir = sysvals.setOutputFolder(value)

	if sysvals.suspendmode == 'command' and not sysvals.testcommand:
		doError('No command supplied for mode "command"')

	# compatibility errors
	if sysvals.usedevsrc and sysvals.usecallgraph:
		doError('-dev is not compatible with -f')
	if sysvals.usecallgraph and sysvals.useprocmon:
		doError('-proc is not compatible with -f')

	if overridekprobes:
		sysvals.tracefuncs = dict()
	if overridedevkprobes:
		sysvals.dev_tracefuncs = dict()

	kprobes = dict()
	kprobesec = 'dev_timeline_functions_'+platform.machine()
	if kprobesec in sections:
		for name in Config.options(kprobesec):
			text = Config.get(kprobesec, name)
			kprobes[name] = (text, True)
	kprobesec = 'timeline_functions_'+platform.machine()
	if kprobesec in sections:
		for name in Config.options(kprobesec):
			if name in kprobes:
				doError('Duplicate timeline function found "%s"' % (name))
			text = Config.get(kprobesec, name)
			kprobes[name] = (text, False)

	for name in kprobes:
		function = name
		format = name
		color = ''
		args = dict()
		text, dev = kprobes[name]
		data = text.split()
		i = 0
		for val in data:
			# bracketted strings are special formatting, read them separately
			if val[0] == '[' and val[-1] == ']':
				for prop in val[1:-1].split(','):
					p = prop.split('=')
					if p[0] == 'color':
						try:
							color = int(p[1], 16)
							color = '#'+p[1]
						except:
							color = p[1]
				continue
			# first real arg should be the format string
			if i == 0:
				format = val
			# all other args are actual function args
			else:
				d = val.split('=')
				args[d[0]] = d[1]
			i += 1
		if not function or not format:
			doError('Invalid kprobe: %s' % name)
		for arg in re.findall('{(?P<n>[a-z,A-Z,0-9]*)}', format):
			if arg not in args:
				doError('Kprobe "%s" is missing argument "%s"' % (name, arg))
		if (dev and name in sysvals.dev_tracefuncs) or (not dev and name in sysvals.tracefuncs):
			doError('Duplicate timeline function found "%s"' % (name))

		kp = {
			'name': name,
			'func': function,
			'format': format,
			sysvals.archargs: args
		}
		if color:
			kp['color'] = color
		if dev:
			sysvals.dev_tracefuncs[name] = kp
		else:
			sysvals.tracefuncs[name] = kp

# Function: printHelp
# Description:
#	 print out the help text
def printHelp():
	print('')
	print('%s v%s' % (sysvals.title, sysvals.version))
	print('Usage: sudo sleepgraph <options> <commands>')
	print('')
	print('Description:')
	print('  This tool is designed to assist kernel and OS developers in optimizing')
	print('  their linux stack\'s suspend/resume time. Using a kernel image built')
	print('  with a few extra options enabled, the tool will execute a suspend and')
	print('  capture dmesg and ftrace data until resume is complete. This data is')
	print('  transformed into a device timeline and an optional callgraph to give')
	print('  a detailed view of which devices/subsystems are taking the most')
	print('  time in suspend/resume.')
	print('')
	print('  If no specific command is given, the default behavior is to initiate')
	print('  a suspend/resume and capture the dmesg/ftrace output as an html timeline.')
	print('')
	print('  Generates output files in subdirectory: suspend-yymmdd-HHMMSS')
	print('   HTML output:                    <hostname>_<mode>.html')
	print('   raw dmesg output:               <hostname>_<mode>_dmesg.txt')
	print('   raw ftrace output:              <hostname>_<mode>_ftrace.txt')
	print('')
	print('Options:')
	print('   -h           Print this help text')
	print('   -v           Print the current tool version')
	print('   -config fn   Pull arguments and config options from file fn')
	print('   -verbose     Print extra information during execution and analysis')
	print('   -m mode      Mode to initiate for suspend (default: %s)') % (sysvals.suspendmode)
	print('   -o name      Overrides the output subdirectory name when running a new test')
	print('                default: suspend-{date}-{time}')
	print('   -rtcwake t   Wakeup t seconds after suspend, set t to "off" to disable (default: 15)')
	print('   -addlogs     Add the dmesg and ftrace logs to the html output')
	print('   -srgap       Add a visible gap in the timeline between sus/res (default: disabled)')
	print('   -skiphtml    Run the test and capture the trace logs, but skip the timeline (default: disabled)')
	print('   -result fn   Export a results table to a text file for parsing.')
	print('  [testprep]')
	print('   -sync        Sync the filesystems before starting the test')
	print('   -rs on/off   Enable/disable runtime suspend for all devices, restore all after test')
	print('   -display on/off  Turn the display on or off for the test')
	print('  [advanced]')
	print('   -gzip        Gzip the trace and dmesg logs to save space')
	print('   -cmd {s}     Run the timeline over a custom command, e.g. "sync -d"')
	print('   -proc        Add usermode process info into the timeline (default: disabled)')
	print('   -dev         Add kernel function calls and threads to the timeline (default: disabled)')
	print('   -x2          Run two suspend/resumes back to back (default: disabled)')
	print('   -x2delay t   Include t ms delay between multiple test runs (default: 0 ms)')
	print('   -predelay t  Include t ms delay before 1st suspend (default: 0 ms)')
	print('   -postdelay t Include t ms delay after last resume (default: 0 ms)')
	print('   -mindev ms   Discard all device blocks shorter than ms milliseconds (e.g. 0.001 for us)')
	print('   -multi n d   Execute <n> consecutive tests at <d> seconds intervals. The outputs will')
	print('                be created in a new subdirectory with a summary page.')
	print('  [debug]')
	print('   -f           Use ftrace to create device callgraphs (default: disabled)')
	print('   -maxdepth N  limit the callgraph data to N call levels (default: 0=all)')
	print('   -expandcg    pre-expand the callgraph data in the html output (default: disabled)')
	print('   -fadd file   Add functions to be graphed in the timeline from a list in a text file')
	print('   -filter "d1,d2,..." Filter out all but this comma-delimited list of device names')
	print('   -mincg  ms   Discard all callgraphs shorter than ms milliseconds (e.g. 0.001 for us)')
	print('   -cgphase P   Only show callgraph data for phase P (e.g. suspend_late)')
	print('   -cgtest N    Only show callgraph data for test N (e.g. 0 or 1 in an x2 run)')
	print('   -timeprec N  Number of significant digits in timestamps (0:S, [3:ms], 6:us)')
	print('   -cgfilter S  Filter the callgraph output in the timeline')
	print('   -cgskip file Callgraph functions to skip, off to disable (default: cgskip.txt)')
	print('   -bufsize N   Set trace buffer size to N kilo-bytes (default: all of free memory)')
	print('')
	print('Other commands:')
	print('   -modes       List available suspend modes')
	print('   -status      Test to see if the system is enabled to run this tool')
	print('   -fpdt        Print out the contents of the ACPI Firmware Performance Data Table')
	print('   -battery     Print out battery info (if available)')
	print('   -sysinfo     Print out system info extracted from BIOS')
	print('   -devinfo     Print out the pm settings of all devices which support runtime suspend')
	print('   -flist       Print the list of functions currently being captured in ftrace')
	print('   -flistall    Print all functions capable of being captured in ftrace')
	print('   -summary dir Create a summary of tests in this dir [-genhtml builds missing html]')
	print('  [redo]')
	print('   -ftrace ftracefile  Create HTML output using ftrace input (used with -dmesg)')
	print('   -dmesg dmesgfile    Create HTML output using dmesg (used with -ftrace)')
	print('  [submit]')
	print('   -submit           Submit the timeline to online DB (requires -dmesg/-ftrace)')
	print('   -submitmulti      Submit timelines from a -multi run (called from inside output folder)')
	print('   -bugreport        Submit a bug report, -desc describes issue (requires -dmesg/-ftrace)')
	print('   -desc "string"    Timeline description to use with -submit (default: "html timeline")')
	print('   -login user pass  Bugzilla user/pass to use with -submit (default: headless account)')
	print('')
	return True

# ----------------- MAIN --------------------
# exec start (skipped if script is loaded as library)
if __name__ == '__main__':
	genhtml = False
	cmd = ''
	simplecmds = ['-sysinfo', '-modes', '-fpdt', '-flist', '-flistall', '-devinfo', '-status', '-battery']
	if '-f' in sys.argv:
		sysvals.cgskip = sysvals.configFile('cgskip.txt')
	db = dict()
	# loop through the command line arguments
	args = iter(sys.argv[1:])
	for arg in args:
		if(arg == '-m'):
			try:
				val = args.next()
			except:
				doError('No mode supplied', True)
			if val == 'command' and not sysvals.testcommand:
				doError('No command supplied for mode "command"', True)
			sysvals.suspendmode = val
		elif(arg in simplecmds):
			cmd = arg[1:]
		elif(arg == '-h'):
			printHelp()
			sys.exit(0)
		elif(arg == '-v'):
			print("Version %s" % sysvals.version)
			sys.exit(0)
		elif(arg == '-x2'):
			sysvals.execcount = 2
		elif(arg == '-x2delay'):
			sysvals.x2delay = getArgInt('-x2delay', args, 0, 60000)
		elif(arg == '-predelay'):
			sysvals.predelay = getArgInt('-predelay', args, 0, 60000)
		elif(arg == '-postdelay'):
			sysvals.postdelay = getArgInt('-postdelay', args, 0, 60000)
		elif(arg == '-f'):
			sysvals.usecallgraph = True
		elif(arg == '-skiphtml'):
			sysvals.skiphtml = True
		elif(arg == '-cgdump'):
			sysvals.cgdump = True
		elif(arg == '-genhtml'):
			genhtml = True
		elif(arg == '-addlogs'):
			sysvals.dmesglog = sysvals.ftracelog = True
		elif(arg == '-verbose'):
			sysvals.verbose = True
		elif(arg == '-proc'):
			sysvals.useprocmon = True
		elif(arg == '-dev'):
			sysvals.usedevsrc = True
		elif(arg == '-sync'):
			sysvals.sync = True
		elif(arg == '-gzip'):
			sysvals.gzip = True
		elif(arg == '-rs'):
			try:
				val = args.next()
			except:
				doError('-rs requires "enable" or "disable"', True)
			if val.lower() in switchvalues:
				if val.lower() in switchoff:
					sysvals.rs = -1
				else:
					sysvals.rs = 1
			else:
				doError('invalid option: %s, use "enable/disable" or "on/off"' % val, True)
		elif(arg == '-display'):
			try:
				val = args.next()
			except:
				doError('-display requires "on" or "off"', True)
			if val.lower() in switchvalues:
				if val.lower() in switchoff:
					sysvals.display = -1
				else:
					sysvals.display = 1
			else:
				doError('invalid option: %s, use "on/off"' % val, True)
		elif(arg == '-maxdepth'):
			sysvals.max_graph_depth = getArgInt('-maxdepth', args, 0, 1000)
		elif(arg == '-rtcwake'):
			try:
				val = args.next()
			except:
				doError('No rtcwake time supplied', True)
			if val.lower() in switchoff:
				sysvals.rtcwake = False
			else:
				sysvals.rtcwake = True
				sysvals.rtcwaketime = getArgInt('-rtcwake', val, 0, 3600, False)
		elif(arg == '-timeprec'):
			sysvals.setPrecision(getArgInt('-timeprec', args, 0, 6))
		elif(arg == '-mindev'):
			sysvals.mindevlen = getArgFloat('-mindev', args, 0.0, 10000.0)
		elif(arg == '-mincg'):
			sysvals.mincglen = getArgFloat('-mincg', args, 0.0, 10000.0)
		elif(arg == '-bufsize'):
			sysvals.bufsize = getArgInt('-bufsize', args, 1, 1024*1024*8)
		elif(arg == '-cgtest'):
			sysvals.cgtest = getArgInt('-cgtest', args, 0, 1)
		elif(arg == '-cgphase'):
			try:
				val = args.next()
			except:
				doError('No phase name supplied', True)
			d = Data(0)
			if val not in d.phasedef:
				doError('invalid phase --> (%s: %s), valid phases are %s'\
					% (arg, val, d.phasedef.keys()), True)
			sysvals.cgphase = val
		elif(arg == '-cgfilter'):
			try:
				val = args.next()
			except:
				doError('No callgraph functions supplied', True)
			sysvals.setCallgraphFilter(val)
		elif(arg == '-cgskip'):
			try:
				val = args.next()
			except:
				doError('No file supplied', True)
			if val.lower() in switchoff:
				sysvals.cgskip = ''
			else:
				sysvals.cgskip = sysvals.configFile(val)
				if(not sysvals.cgskip):
					doError('%s does not exist' % sysvals.cgskip)
		elif(arg == '-callloop-maxgap'):
			sysvals.callloopmaxgap = getArgFloat('-callloop-maxgap', args, 0.0, 1.0)
		elif(arg == '-callloop-maxlen'):
			sysvals.callloopmaxlen = getArgFloat('-callloop-maxlen', args, 0.0, 1.0)
		elif(arg == '-cmd'):
			try:
				val = args.next()
			except:
				doError('No command string supplied', True)
			sysvals.testcommand = val
			sysvals.suspendmode = 'command'
		elif(arg == '-expandcg'):
			sysvals.cgexp = True
		elif(arg == '-srgap'):
			sysvals.srgap = 5
		elif(arg == '-multi'):
			sysvals.multitest['run'] = True
			sysvals.multitest['count'] = getArgInt('-multi n d (exec count)', args, 2, 1000000)
			sysvals.multitest['delay'] = getArgInt('-multi n d (delay between tests)', args, 0, 3600)
		elif(arg == '-o'):
			try:
				val = args.next()
			except:
				doError('No subdirectory name supplied', True)
			sysvals.outdir = sysvals.setOutputFolder(val)
		elif(arg == '-config'):
			try:
				val = args.next()
			except:
				doError('No text file supplied', True)
			file = sysvals.configFile(val)
			if(not file):
				doError('%s does not exist' % val)
			configFromFile(file)
		elif(arg == '-fadd'):
			try:
				val = args.next()
			except:
				doError('No text file supplied', True)
			file = sysvals.configFile(val)
			if(not file):
				doError('%s does not exist' % val)
			sysvals.addFtraceFilterFunctions(file)
		elif(arg == '-dmesg'):
			try:
				val = args.next()
			except:
				doError('No dmesg file supplied', True)
			sysvals.notestrun = True
			sysvals.dmesgfile = val
			if(os.path.exists(sysvals.dmesgfile) == False):
				doError('%s does not exist' % sysvals.dmesgfile)
		elif(arg == '-ftrace'):
			try:
				val = args.next()
			except:
				doError('No ftrace file supplied', True)
			sysvals.notestrun = True
			sysvals.ftracefile = val
			if(os.path.exists(sysvals.ftracefile) == False):
				doError('%s does not exist' % sysvals.ftracefile)
		elif(arg == '-summary'):
			try:
				val = args.next()
			except:
				doError('No directory supplied', True)
			cmd = 'summary'
			sysvals.outdir = val
			sysvals.notestrun = True
			if(os.path.isdir(val) == False):
				doError('%s is not accesible' % val)
		elif(arg == '-filter'):
			try:
				val = args.next()
			except:
				doError('No devnames supplied', True)
			sysvals.setDeviceFilter(val)
		elif(arg == '-submit'):
			sysvals.notestrun = True
			db['submit'] = 'single'
		elif(arg == '-submitmulti'):
			sysvals.notestrun = True
			db['submit'] = 'multi'
		elif(arg == '-bugreport'):
			sysvals.notestrun = True
			db['submit'] = db['extra'] = 'bugreport'
		elif(arg == '-login'):
			try:
				db['user'] = args.next()
				db['pass'] = args.next()
			except:
				doError('Missing username and password', True)
		elif(arg == '-desc'):
			try:
				db['desc'] = args.next()
			except:
				doError('Missing description', True)
		elif(arg == '-result'):
			try:
				val = args.next()
			except:
				doError('No result file supplied', True)
			sysvals.result = val
		else:
			doError('Invalid argument: '+arg, True)

	# compatibility errors
	if(sysvals.usecallgraph and sysvals.usedevsrc):
		doError('-dev is not compatible with -f')
	if(sysvals.usecallgraph and sysvals.useprocmon):
		doError('-proc is not compatible with -f')

	if sysvals.usecallgraph and sysvals.cgskip:
		sysvals.vprint('Using cgskip file: %s' % sysvals.cgskip)
		sysvals.setCallgraphBlacklist(sysvals.cgskip)

	# callgraph size cannot exceed device size
	if sysvals.mincglen < sysvals.mindevlen:
		sysvals.mincglen = sysvals.mindevlen

	# remove existing buffers before calculating memory
	if(sysvals.usecallgraph or sysvals.usedevsrc):
		sysvals.fsetVal('16', 'buffer_size_kb')
	sysvals.cpuInfo()

	# just run a utility command and exit
	if(cmd != ''):
		ret = 0
		if(cmd == 'status'):
			if not statusCheck(True):
				ret = 1
		elif(cmd == 'fpdt'):
			if not getFPDT(True):
				ret = 1
		elif(cmd == 'battery'):
			out = getBattery()
			if out:
				print 'AC Connect    : %s\nBattery Charge: %d' % out
			else:
				print 'no battery found'
				ret = 1
		elif(cmd == 'sysinfo'):
			sysvals.printSystemInfo(True)
		elif(cmd == 'devinfo'):
			deviceInfo()
		elif(cmd == 'modes'):
			print getModes()
		elif(cmd == 'flist'):
			sysvals.getFtraceFilterFunctions(True)
		elif(cmd == 'flistall'):
			sysvals.getFtraceFilterFunctions(False)
		elif(cmd == 'summary'):
			runSummary(sysvals.outdir, True, genhtml)
		sys.exit(ret)

	# if instructed, re-analyze existing data files
	if(sysvals.notestrun):
		if 'submit' in db:
			db['apikey'] = base64.b64decode('aHM5RzZmR3lrcWNQRUo5N2ExWDVRTTE2Uk01U0RHS2RZWHpuclR1Mg==')
			if 'user' not in db or 'pass' not in db:
				db['user'] = base64.b64decode('c2xlZXBncmFwaC10b29s')
				db['pass'] = base64.b64decode('aGVhZGxlc3M=')
			if db['submit'] == 'single':
				if not sysvals.dmesgfile or not sysvals.ftracefile:
					doError('submit requires both -dmesg and -ftrace')
				submit, stamp, htmlfile = rerunTest(db)
				out = submitTimeline(submit, stamp, [htmlfile])
				stamp['bugurl'] = out['bugurl']
				sysvals.outputResult(stamp)
				os.remove(htmlfile)
			elif db['submit'] == 'bugreport':
				if not sysvals.dmesgfile or not sysvals.ftracefile:
					doError('bugreport requires both -dmesg and -ftrace')
				bugReport(sysvals, db)
			elif db['submit'] == 'multi':
				if not os.path.exists('summary.html'):
					doError('submitmulti must be run inside a -multi output folder (cannot find summary.html)')
				submitMultiTimeline('summary.html', db)
		else:
			submit, stamp, htmlfile = rerunTest()
			sysvals.outputResult(stamp)
		sys.exit(0)

	# verify that we can run a test
	if(not statusCheck()):
		doError('Check FAILED, aborting the test run!')

	# extract mem modes and convert
	mode = sysvals.suspendmode
	if 'mem' == mode[:3]:
		if '-' in mode:
			memmode = mode.split('-')[-1]
		else:
			memmode = 'deep'
		if memmode == 'shallow':
			mode = 'standby'
		elif memmode ==  's2idle':
			mode = 'freeze'
		else:
			mode = 'mem'
		sysvals.memmode = memmode
		sysvals.suspendmode = mode

	sysvals.systemInfo(dmidecode(sysvals.mempath))

	setRuntimeSuspend(True)
	if sysvals.display:
		call('xset -d :0.0 dpms 0 0 0', shell=True)
		call('xset -d :0.0 s off', shell=True)
	ret = 0
	if sysvals.multitest['run']:
		# run multiple tests in a separate subdirectory
		if not sysvals.outdir:
			s = 'suspend-x%d' % sysvals.multitest['count']
			sysvals.outdir = datetime.now().strftime(s+'-%y%m%d-%H%M%S')
		if not os.path.isdir(sysvals.outdir):
			os.mkdir(sysvals.outdir)
		for i in range(sysvals.multitest['count']):
			if(i != 0):
				print('Waiting %d seconds...' % (sysvals.multitest['delay']))
				time.sleep(sysvals.multitest['delay'])
			print('TEST (%d/%d) START' % (i+1, sysvals.multitest['count']))
			fmt = 'suspend-%y%m%d-%H%M%S'
			sysvals.testdir = os.path.join(sysvals.outdir, datetime.now().strftime(fmt))
			ret = runTest(i+1)
			print('TEST (%d/%d) COMPLETE' % (i+1, sysvals.multitest['count']))
			sysvals.logmsg = ''
		if not sysvals.skiphtml:
			runSummary(sysvals.outdir, False, False)
		sysvals.sudouser(sysvals.outdir)
	else:
		if sysvals.outdir:
			sysvals.testdir = sysvals.outdir
		# run the test in the current directory
		ret = runTest()
	if sysvals.display:
		call('xset -d :0.0 s reset', shell=True)
	setRuntimeSuspend(False)
	sys.exit(ret)
