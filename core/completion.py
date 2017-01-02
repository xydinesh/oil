#!/usr/bin/env python3
# Copyright 2016 Andy Chu. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
"""
completion.py - Tab completion.

TODO: Is this specific to osh/oil, or common?

Architecture:

Completion should run in threads?  For two reasons:

- Completion can be slow -- e.g. completion for distributed resources
- Because readline has a weird interface, and then you can implement
  "iterators" in C++ or oil.  They just push onto a PIPE.  Use a netstring
  protocol and self-pipe?
- completion can be in another process anyway?

Does that mean the user code gets run in an entirely separate interpreter?  The
whole lexer/parser/cmd_exec combo has to be thread-safe.  Does it get a copy of
the same startup state?

bash note: most of this stuff is in pcomplete.c and bashline.c (4K lines!).
Uses ITEMLIST with a bunch of flags.
"""

import atexit
import fnmatch
import readline
import os
import stat
import sys
import time
import traceback

from osh import ast
from osh import parse_lib
from core import ui
from core import util
from core.id_kind import Id

command_e = ast.command_e


class CompletionLookup(object):
  """
  names -> list of actions

  -E -> list of actions
  -D -> default list of actions, when we don't know

  Maybe call those __DEFAULT__ and '' or something, -D and -E is
  confusing.

  But I also want to register patterns.
  """
  def __init__(self):
    # default default actions are what?  Rest completer are filenames!
    # TODO:
    default_default = ChainedCompleter([])
    self.lookup = {'__default__': default_default}

    # NOTE: These two are the same by default, and bash provides a way to
    # change empty_comp, with -E.  We will provide a way to change first_comp
    # too.  If set, empty_comp also uses it?  That only makes sense.
    self.empty_comp = None
    self.first_comp = None

    # So you can register *.sh, unlike bash.  List of (glob, [actions]),
    # searched linearly.
    self.patterns = []

  def RegisterName(self, name, chain):
    """
    Called by 'complete' builtin.

    Args:
      name: command name, '' for empty, or __default__ for default?
      actions: list of CompAction instances
    """
    self.lookup[name] = chain

  def RegisterGlob(self, glob_pat, chain):
    self.patterns.append((glob_pat, chain))

  def RegisterEmpty(self, chain):
    """What to do when completing empty line."""
    self.empty_comp = chain

  def GetEmptyCompleter(self):
    return self.empty_comp

  def RegisterFirst(self, chain):
    """
    What to do when complete the first word.  By default, there are 5 actions.

    bash doesn't provide a way to change this -- it only provides -E.
    """
    self.first_comp = chain

  def GetFirstCompleter(self):
    return self.first_comp

  def GetCompleterForName(self, argv0):
    """
    Args:
      argv0: A finished argv0 to lookup
    """
    if not argv0:
      return self.empty_comp

    chain = self.lookup.get(argv0)  # NOTE: Could be ''
    if chain:
      return chain

    key = os.path.basename(argv0)
    actions = self.lookup.get(key)
    if chain:
      return chain

    for glob_pat, chain in self.patterns:
      if fnmatch.fnmatch(key, glob_pat):
        return chain

    return self.lookup['__default__']


#
# Actions
#

class CompletionAction(object):
  """Returns a list of words.

  Function
  Literal words
  """
  def __init__(self):
    pass

  def Matches(self, words, index, prefix):
    # Prefix is the current one?
    # What if the cursor is not at the end of line?  See readline interface.
    pass


class WordsAction(CompletionAction):
  # NOTE: Have to split the words passed to -W.  Using IFS or something else?
  def __init__(self, words, delay=None):
    self.words = words
    self.delay = delay

  def Matches(self, words, index, prefix):
    for w in self.words:
      if w.startswith(prefix):
        if self.delay:
          time.sleep(self.delay)
        yield w + ' '


class LiveDictAction(CompletionAction):
  def __init__(self, d):
    self.d = d

  def Matches(self, words, index, prefix):
    for name in sorted(self.d):
      if name.startswith(prefix):
        yield name + ' '  # full word


class ShellFuncAction(CompletionAction):
  def __init__(self, ex, func):
    self.ex = ex
    self.func = func

  def Matches(self, words, index, prefix):
    # TODO:
    # - Set COMP_CWORD etc. in ex.mem -- in the global namespace I guess
    # - Then parse the reply here

    # This is like a stack code:
    # for word in words:
    #   self.ex.PushString(word)
    # self.ex.PushString('COMP_WORDS')
    # self.ex.MakeArray()

    # self.ex.PushString(str(index))
    # self.ex.PushString('COMP_CWORD')

    # TODO: Get the name instead!
    # self.ex.PushString(self.func_name)
    # self.ex.Call()  # call wit no arguments

    # self.ex.PushString('COMP_REPLY')

    # How does this one work?
    # reply = []
    # self.ex.GetArray(reply)

    self.ex.mem.SetGlobalArray('COMP_WORDS', words)
    self.ex.mem.SetGlobalString('COMP_CWORD', str(index))

    self.ex.RunFunc(self.func, [])  # call with no arguments

    # Should be COMP_REPLY to follow naming convention!  Lame.
    defined, val = self.ex.mem.GetGlobal('COMPREPLY')
    if not defined:
      print('COMP_REPLY not defined', file=sys.stderr)
      return

    is_array, reply = val.AsArray()
    if not is_array:
      print('ERROR: COMP_REPLY should be an array, got %s', file=sys.stderr)
      return

    print('REPLY', reply)
    #reply = ['g1', 'g2', 'h1', 'i1']
    for name in sorted(reply):
      if name.startswith(prefix):
        yield name + ' '  # full word


class VarAction(object):
  """Completes variable names."""

  def __init__(self, environ, mem):
    """
    Args:
      mem: cmd_exec.Mem object
    """
    self.environ = environ
    # How to complete environment?  **environ global var.  That's os.environ.
    # What about lo
    self.mem = mem

  def Matches(self, words, index, prefix):
    assert prefix.startswith('$')
    prefix = prefix[1:]
    for name in sorted(self.environ):
      if name.startswith(prefix):
        yield '$' + name + ' '  # full word


class ExternalCommandAction(object):
  """Complete commands in $PATH.

  NOTE: -A command in bash is FIVE things: aliases, builtins, functions,
  keywords, etc.
  """
  def __init__(self, mem):
    """
    Args:
      mem: for looking up Path
    """
    self.mem = mem
    # Should we list everything executable in $PATH here?  And then whenever
    # $PATH is changed, regenerated it?
    # Or we can cache directory listings?  What if the contents of the dir
    # changed?
    # Can we look at the dir timestamp?
    #
    # (dir, timestamp) -> list of entries perhaps?  And then every time you hit
    # tab, do you have to check the timestamp?  It should be cached by the
    # kernel, so yes.
    self.ext = []

    # (dir, timestamp) -> list
    # NOTE: This cache assumes that listing a directory is slower than statting
    # it to get the mtime.  That may not be true on all systems?  Either way
    # you are reading blocks of metadata.  But I guess /bin on many systems is
    # huge, and will require lots of sys calls.
    self.cache = {}

  def Matches(self, words, index, prefix):
    """
    TODO: Cache is never cleared.

    - When we get a newer timestamp, we should clear the old one.
    - When PATH is changed, we can remove old entries.
    """
    defined, val = self.mem.Get('PATH')
    if not defined:
      # No matches if not defined
      return
    is_str, path = val.AsString()
    if not is_str:
      # No matches if not a string
      return
    path_dirs = path.split(':')
    #print(path_dirs)

    names = []
    for d in path_dirs:
      st = os.stat(d)
      key = (d, st.st_mtime)
      listing = self.cache.get(key)
      if listing is None:
        listing = os.listdir(d)
        self.cache[key] = listing
      names.extend(listing)

    for word in listing:
      if word.startswith(prefix):
        yield word + ' '


class GlobPredicate(object):
  """Expand into files that match a pattern.  !*.py filters them.

  Weird syntax:
  *.py or !*.py

  Also & is a placeholder for the string being completed?.  Yeah I probably
  want to get rid of this feature.
  """
  def __init__(self, glob_pat):
    self.glob_pat = glob_pat

  def __call__(self, match):
    return fnmatch.fnmatch(match, self.glob_pat)


class ChainedCompleter(object):
  """
  Composite completer, composed of individual ones.

  NOTE: plus_dirs happens AFTER filtering with predicates?  We add BACK the
  dirs, e.g. -A file -X '!*.sh' -o plusdirs.

  NOTE: plusdirs can just create another chained completer.  I think you should
  probably get rid of the predicate.  That should just be a Filter().  prefix
  and suffix can be adhoc for now I guess, since they are trivial.
  """
  def __init__(self, actions, predicate=None, prefix='', suffix=''):
    self.actions = actions
    self.predicate = predicate or (lambda word: True)
    self.prefix = prefix
    self.suffix = suffix

  def Matches(self, words, index, prefix):
    for a in self.actions:
      for match in a.Matches(words, index, prefix):
        # There are two kinds of filters: changing the string, and filtering
        # the set of strings.

        # So maybe have modifiers AND filters?  A triple.
        if self.predicate(match):
          yield self.prefix + match + self.suffix

    # Prefix is the current one?

    # What if the cursor is not at the end of line?  See readline interface.
    # That's OK -- we just truncate the line at the cursor?
    # Hm actually zsh does something smarter, and which is probably preferable.
    # It completes the word that


class DummyParser(object):
  def GetWords(self, buf):
    words = buf.split()
    # 'grep ' -> ['grep', ''], so we're completing the second word
    if buf.endswith(' '):
      words.append('')
    return words


def _FindLastSimpleCommand(node):
  """
  The last thing has to be a simple command.  Cases:

  echo a; echo b
  ls | wc -l
  test -f foo && hello
  """
  if node.tag == command_e.SimpleCommand:
    return node

  assert hasattr(node, 'children'), node

  n = len(node.children)
  if n == 0:
    return None

  # Go as deep as we need.
  return _FindLastSimpleCommand(node.children[-1])


ECompletionType = util.Enum('ECompletionState',
    'NONE FIRST REST VAR_NAME HASH_KEY REDIR_FILENAME'.split())
# REDIR_FILENAME: stdin only
# HASH_KEY: do this later
# NONE: nothing detected, should we default to filenames/directories?

# Note: this could also be a CompRequest
# CompRequest(FIRST, prefix)
# CompRequest(REST, prefix, comp_words)  # use the full contact
# CompRequest(VAR_NAME, prefix)
# CompRequest(HASH_KEY, prefix, hash_name)  # use var name
# CompRequest(REDIR_FILENAME, prefix)

# Or it could just be a Completer / Chain?
# CommandCompleter

# ShellFuncAction()
#  CompRequest?
#
# ShellFuncAction.Complete(words, i, prefix) -> iter
#   This is the REST completer
# VarCompleter.Complete(prefix) -> iter
# HashKey.Complete(hash_name, prefix) -> iter

def _GetCompletionType(w_parser, c_parser, ev, status_lines):
  """
  Parser returns completion state.
  Then we translate that into ECompletionType.

  Returns:
    comp_type
    prefix: the prefix to complete
    comp_words: list of words.  First word is used for dispatching.

    TODO: what about hash table name?
  """
  node = c_parser.ParseCommandLine()

  # Inspect state after parsing.  Hm I'm getting the newline.  Can I view the
  # one before that?
  cur_token = w_parser.cur_token
  prev_token = w_parser.PrevToken()
  cur_word = w_parser.cursor
  comp_state = c_parser.GetCompletionState()

  com_node = None
  if node:
    # These 4 should all parse
    if node.tag == command_e.SimpleCommand:
      # NOTE: prev_token can be ;, then complete a new one
      #print('WORDS', node.words)
      # TODO:
      # - EvalVarSub depends on memory
      # - EvalTildeSub needs to be somewhere else
      # - EvalCommandSub needs to be
      #
      # maybe write a version of Executor._EvalWords that doesn't do
      # CommandSub.  Or honestly you can just reuse it for now.  Can you pass
      # the same cmd_exec in?  What about side effects?  I guess it can't
      # really have any.  It can only have them on the file system.  Hm.
      # Defining funcitons?  Yeah if you complete partial functions that could
      # be bad.  That is, you could change the name of the function.

      ifs = ''
      do_glob = True  # this is from the cmd_exec.  $foo/bar could be split up.
                      # Does that matter for us?  Yes it can matter if $foo is
                      # '-a bar'!  Because then flag parsing will be different.
      argv = []
      for w in node.words:
        ok, val = ev.EvalCompoundWord(w, ifs, do_glob)
        if not ok:
          # Why would it fail?
          continue
        is_str, s = val.AsString()
        if is_str:
          argv.append(s)
        else:
          pass
          # Oh I have to handle $@ on the command line?

      print(argv)
      com_node = node

    elif node.tag == command_e.Block:  # echo a; echo b
      com_node = _FindLastSimpleCommand(node)
    elif node.tag == command_e.AndOr:  # echo a && echo b
      com_node = _FindLastSimpleCommand(node)
    elif node.tag == command_e.Pipeline :  # echo a | wc -l
      com_node = _FindLastSimpleCommand(node)
    else:
      # Return NONE?  Not handling it for now
      pass
  else:  # No node.
    pass

  # TODO: Need to show buf... Need a multiline display for debugging?
  s1 = status_lines[1]
  s2 = status_lines[2]
  s3 = status_lines[3]
  s6 = status_lines[6]

  s1.Write('prev_token %s  cur_token %s  cur_word %s', prev_token, cur_token,
      cur_word)
  s2.Write('comp_state %s  error %s', comp_state, c_parser.Error())
  # This one can be multiple lines
  s3.Write('node: %s %s',
      node.DebugString() if node else '<Parse Error>',
      node.tag if node else '')
  # This one can be multiple lines
  s6.Write('com_node: %s', com_node.DebugString() if com_node else '<None>')

  # TODO: Fill these in
  comp_type = ECompletionType.FIRST
  prefix = ''
  words = []

  # IMPORTANT: if the last token is Id.Ignored_Space, then we want to add a
  # dummy word!  empty word

  # initial simple algorithm
  # If we got a node:
  #   1. Look at c_parser.LastCompletionState()
  #   1. don't complete unless it's SIMPLE_COMMAND?
  #   2. look at node.words -- first word or not?
  #   3. EvalStatic() of the first word

  # If we got None:
  #   1. Look at c_parser.LastCompletionState()
  #   2. If it is $ or ${, complete var names
  #
  #   Is there any case where we shoudl fall back on buf.split()?

  # Now parse it.  And then look at the AST, but don't eval?  Or actually we
  # CAN eval, but we probably don't want to.
  #
  # completion state also has to know about ${pre<TAB>  and ${foo[pre<TAB>
  # Those are invalid parses.  But the LAST TOKEN is the one we want to
  # complete?  Will it be a proper group of LIT tokens?  I don't think you
  # complete anything else besides that?
  #
  # $<TAB> will be Id.Lit_Other -- but you might want to special case
  # $na<TAB> will be VS_NAME

  # NOTE: The LineLexer adds \n to the buf?  Should we disable it and add \0?

  # I guess the shortest way to do it is to just Eval(), and even run command
  # sub.  Or maybe SafeEval() for command sub returns __DUMMY__ or None or some
  # other crap.
  # I guess in oil you could have some arbitrarily long function in $split(bar,
  # baz).  That is what you would want to run the completion in a subprocess
  # with a timeout.
  return comp_type, prefix, words


def _GetCompletionType1(parser, buf):
  words = parser.GetWords(buf)
  comp_name = None

  n = len(words)
  # Complete variables
  # TODO: Parser should tell if we saw $, ${, but are NOT in a single quoted
  # state.  And also we didn't see $${, which would be a special var.  Oil
  # rules are almost the same.
  if n > 0 and words[-1].startswith('$'):
    comp_type = ECompletionType.VAR_NAME
    prefix = words[-1]

  # Otherwise complete words
  elif n == 0:
    comp_type = ECompletionType.FIRST
    prefix = ''
  elif n == 1:
    comp_type = ECompletionType.FIRST
    prefix = words[-1]
  else:
    comp_type = ECompletionType.REST
    prefix = words[-1]

  comp_index = len(words) - 1
  return comp_type, prefix, words


class RootCompleter(object):
  """
  Provide completion of a buffer according to the configured rules.
  """
  def __init__(self, make_parser, ev, comp_lookup, var_comp):
    self.make_parser = make_parser
    self.ev = ev
    self.comp_lookup = comp_lookup
    # This can happen in any position, with any command
    self.var_comp = var_comp

    self.parser = DummyParser()  # TODO: remove

  def Matches(self, buf, status_lines):
    w_parser, c_parser = self.make_parser(buf)
    comp_type, prefix, comp_words = _GetCompletionType(
        w_parser, c_parser, self.ev, status_lines)

    comp_type, prefix, comp_words = _GetCompletionType1(self.parser, buf)

    # TODO: I don't get bash -D vs -E.  Might need to write a test program.

    if comp_type == ECompletionType.VAR_NAME:
      # Non-user chain
      chain = self.var_comp
    elif comp_type == ECompletionType.HASH_KEY:
      # Non-user chain
      chain = 'TODO'
    elif comp_type == ECompletionType.REDIR_FILENAME:
      # Non-user chain
      chain = 'TODO'

    elif comp_type == ECompletionType.FIRST:
      chain = self.comp_lookup.GetFirstCompleter()
    elif comp_type == ECompletionType.REST:
      chain = self.comp_lookup.GetCompleterForName(comp_words[0])

    elif comp_type == ECompletionType.NONE:
      # Null chain?  No completion?  For example,
      # ${a:- <TAB>  -- we have no idea what to put here
      chain = 'TODO'
    else:
      raise AssertionError(comp_type)

    status_lines[0].Write('Completing %r ... (Ctrl-C to cancel)', buf)
    start_time = time.time()

    index = len(comp_words) - 1  # COMP_CWORD -1 when it's empty
    i = 0
    for m in chain.Matches(comp_words, index, prefix):
      # TODO: need to dedupe these
      yield m
      i += 1
      elapsed = time.time() - start_time
      plural = '' if i == 1 else 'es'
      status_lines[0].Write(
          '... %d match%s for %r in %.2f seconds (Ctrl-C to cancel)', i,
          plural, buf, elapsed)

    elapsed = time.time() - start_time
    plural = '' if i == 1 else 'es'
    status_lines[0].Write(
        'Found %d match%s for %r in %.2f seconds', i,
        plural, buf, elapsed)

    # TODO: Have to de-dupe and sort these?  Because 'echo' is a builtin as
    # well as a command, and we don't want to show it twice.  Although then
    # it's not incremental.  We can still show progress though.  Need
    # status_line.


class ReadlineCompleter(object):
  def __init__(self, root_comp, status_lines, debug=False):
    self.root_comp = root_comp
    self.status_lines = status_lines
    self.debug = debug

    self.comp_iter = None  # current completion being processed

  def _GetNextCompletion(self, word, state):
    if state == 0:
      # TODO: Tokenize it according to our language
      buf = readline.get_line_buffer()

      # Begin: the index of the first char of the 'word' in the line.  Words
      # are parsed according to readline delims (which we won't use).

      begin = readline.get_begidx()

      # The current position of the cursor.  The thing being completed.
      end = readline.get_endidx()

      if self.debug:
        self.status_line.Write(
            'line: %r / begin - end: %d - %d, part: %r', buf, begin, end,
            buf[begin:end])

      self.comp_iter = self.root_comp.Matches(buf, self.status_lines)

    if self.comp_iter is None:
      self.status_line.Write("ASSERT comp_iter shouldn't be None")

    try:
      next_completion = self.comp_iter.__next__()
    except StopIteration:
      next_completion = None  # sentinel?

    return next_completion

  def __call__(self, word, state):
    """Return a single match."""
    # The readline library swallows exceptions.  We have to display them
    # ourselves.
    try:
      return self._GetNextCompletion(word, state)
    except Exception as e:
      traceback.print_exc()
      self.status_line.Write('Unhandled exception while completing: %s', e)


def InitReadline(complete_cb):
  history_filename = os.path.join(os.environ['HOME'], 'oil_history')

  try:
    readline.read_history_file(history_filename)
  except IOError:
    pass

  atexit.register(readline.write_history_file, history_filename)
  readline.parse_and_bind("tab: complete")

  # How does this map to C?
  # https://cnswww.cns.cwru.edu/php/chet/readline/readline.html#SEC45

  readline.set_completer(complete_cb)

  # NOTE: This apparently matters for -a -n completion -- why?  Is space the
  # right value?
  # http://web.mit.edu/gnu/doc/html/rlman_2.html#SEC39
  # "The basic list of characters that signal a break between words for the
  # completer routine. The default value of this variable is the characters
  # which break words for completion in Bash, i.e., " \t\n\"\\'`@$><=;|&{(""
  #
  # Hm I don't get this.
  readline.set_completer_delims(' ')


def Init(builtins, mem, funcs, comp_lookup, status_lines, ev):

  aliases_action = WordsAction(['TODO:alias'])
  commands_action = ExternalCommandAction(mem)
  builtins_action = WordsAction(builtins.GetNamesToComplete())
  keywords_action = WordsAction(['TODO:keywords'])
  funcs_action = LiveDictAction(funcs)

  first_chain = ChainedCompleter([
      aliases_action, commands_action, builtins_action, keywords_action,
      funcs_action
  ])

  # NOTE: These two are the same by default
  comp_lookup.RegisterEmpty(first_chain)
  comp_lookup.RegisterFirst(first_chain)

  # NOTE: Need set_completer_delims to be space here?  Otherwise you complete
  # as --a and --n.  Why?
  comp_lookup.RegisterName('__default__', WordsAction(['-a', '-n']))

  A1 = WordsAction(['foo.py', 'foo', 'bar.py'])
  A2 = WordsAction(['m%d' % i for i in range(5)], delay=0.1)
  C1 = ChainedCompleter([A1, A2])
  comp_lookup.RegisterName('grep', C1)

  make_parser = parse_lib.MakeParserForCompletion
  var_comp = VarAction(os.environ, mem)
  root_comp = RootCompleter(make_parser, ev, comp_lookup, var_comp)

  complete_cb = ReadlineCompleter(root_comp, status_lines)
  InitReadline(complete_cb)


if __name__ == '__main__':
  from builtin import Builtins
  import cmd_exec

  status_lines = ui.MakeStatusLines()

  builtins = Builtins(status_lines[0])
  mem = cmd_exec.Mem('dummy', [])

  funcs = {'func1': None, 'func2': None, 'exfunc': None}
  comp_lookup = CompletionLookup()
  ev = None

  Init(builtins, mem, funcs, comp_lookup, status_lines, ev)

  # Disable it.  OK so this is how you go back and forth?  At least in Python.
  # Enable and disable custom completer?
  # Is this an action then?  It doesn't really fit into the framework.
  #
  # -A file does this ... TODO: Look at bash source code for that?
  # Yup, look at bashline.c.  It does save and restore.  This is pretty lame.

  try:
    if sys.argv[1] == 'filename':
      print('Disabling completer')
      readline.set_completer(None)
  except IndexError:
    pass

  while True:
    s = input('! ')
    print(s)
