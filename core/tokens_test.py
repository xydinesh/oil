#!/usr/bin/env python3
# Copyright 2016 Andy Chu. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
"""
tokens_test.py: Tests for tokens.py
"""

import unittest

import tokens
from tokens import Id, IdName, Kind

from lexer import Token


class TokensTest(unittest.TestCase):

  def testId(self):
    print(dir(Id))
    print(Id.Op_Newline)
    print(Id.Undefined_Tok)

  def testTokens(self):
    print(Id.Op_Newline)
    print(Token(Id.Op_Newline, '\n'))

    print(IdName(Id.Op_Newline))

    print(Kind.Eof)
    print(Kind.Left)
    print('--')
    for name in dir(Kind):
      if name[0].isupper():
        print(name, getattr(Kind, name))

    # Make sure we're not exporting too much
    print(dir(tokens))

    # 144 out of 256 tokens now
    print(len(tokens._ID_NAMES))

    t = Token(Id.Arith_Plus, '+')
    self.assertEqual(Kind.Arith, t.Kind())
    t = Token(Id.Arith_CaretEqual, '^=')
    self.assertEqual(Kind.Arith, t.Kind())
    t = Token(Id.Arith_RBrace, '}')
    self.assertEqual(Kind.Arith, t.Kind())

    t = Token(Id.BoolBinary_DEqual, '==')
    self.assertEqual(Kind.BoolBinary, t.Kind())

  def testBoolLexerPairs(self):
    lookup = dict(tokens.ID_SPEC.BoolLexerPairs())
    print(lookup)
    self.assertEqual(Id.BoolUnary_a, lookup['\-a'])
    self.assertEqual(Id.BoolUnary_z, lookup['\-z'])
    self.assertEqual(Id.BoolBinary_eq, lookup['\-eq'])


def PrintBoolTable():
  for i, (logical, arity, arg_type) in tokens.BOOL_OPS.items():
    row = (tokens.IdName(i), logical, arity, arg_type)
    print('\t'.join(str(c) for c in row))


if __name__ == '__main__':
  import sys
  if len(sys.argv) > 1 and sys.argv[1] == 'stats':
    k = tokens._kind_sizes
    print('STATS: %d tokens in %d groups: %s' % (sum(k), len(k), k))
    # Thinking about switching
    big = [i for i in k if i > 8]
    print('%d BIG groups: %s' % (len(big), big))

    PrintBoolTable()

  else:
    unittest.main()
