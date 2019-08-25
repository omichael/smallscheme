#!/usr/bin/env python

import lark
import operator
import re
from functools import reduce

parser = lark.Lark(
    '''
start       : _expr
_exprs      : ( _expr _whitespace )* _expr
_expr       : ATOM
            | _num
            | BOOL
            | list
TRUE        : "#t"
FALSE       : "#f"
BOOL        : TRUE | FALSE
list        : "(" _exprs? ")"
ATOM        : /[a-zA-Z]+[a-zA-Z0-9\-\?]*/
            | /[\+\*\/\-\=\>\<]/
INT         : /[-+]?[0-9]+/
FLOAT       : /[-+]?[0-9]+\.[0-9]*/
_num        : INT | FLOAT
_whitespace : (" " | /\t/ )+
    ''')

def convert_ast(ast):
    if type(ast) is lark.tree.Tree:
        if ast.data == "start":
            return convert_ast(ast.children[0])
        if ast.data == "list":
            return ('list', [convert_ast(x) for x in ast.children])
    if type(ast) is lark.lexer.Token:
        ty = ast.type.lower()
        if ty == "int":
            return (ty, int(ast.value))
        if ty == "float":
            return (ty, float(ast.value))
        elif ty == "bool":
            return (ty, True if ast.value == "#t" else False)
        else:
            return (ast.type.lower(), ast.value)
    raise Exception("Unparsed AST: '%s'" % ast)

def parse_str(x):
    # A bit of a hack, maybe handle newlines directly in parser:
    return convert_ast(parser.parse(x.replace("\n", " ")))

def argstype(args):
    # FIXME: Unit test for argument types
    arglist = [x for (x, _) in args]
    argset = set(arglist)
    if argset == {'int', 'float'} or argset == {'float'}:
        return 'float'
    elif argset == {'int'}:
        return 'int'
    else:
        raise Exception("Bad numeric arg list: '%s'"
                        % arglist)

def plus(args):
    return (argstype(args),
            sum(x for (_, x) in args))

def times(args):
    return (argstype(args),
            reduce(operator.mul,
                   (x for (_, x) in args),
                   1))

def minus(args):
    if len(args) == 1:
        return argstype(args), -args[0][1]
    else:
        return (argstype(args),
                args[0][1] - sum(x for (_, x) in args[1:]))

def divide(args):
    return (argstype(args),
            args[0][1] // reduce(operator.mul,
                                 (x for (_, x) in args[1:]),
                                 1))

def equals(args):
    return ('bool', operator.eq(*args))

def compare(args, oper):
    ((ty1, v1), (ty2, v2)) = args[:2]
    if ((ty1 != 'int' and ty1 != 'float') or
        (ty2 != 'int' and ty2 != 'float')):
        raise Exception("Type error, can't compare '%s' to '%s'!" %
                        (ty1, ty2))
    return ('bool', oper(v1, v2))

def lessthan(args):
    return compare(args, operator.lt)

def greaterthan(args):
    return compare(args, operator.gt)

def notnot(args):
    if args[0] == ('bool', False):
        return ('bool', True)
    else:
        return ('bool', False)

def car(x):
    typ, l = x[0]
    if typ != 'list':
        raise Exception("Can't take car of '%s'!"
                        % x)
    return l[0]

def cdr(x):
    typ, l = x[0]
    if typ != 'list':
        raise Exception("Can't take car of '%s'!"
                        % x)
    return ('list', l[1:])

dispatch_table = {'+': plus,
                  '*': times,
                  '-': minus,
                  '/': divide,
                  '=': equals,
                  '<': lessthan,
                  '>': greaterthan,
                  'not': notnot,
                  'car': car,
                  'cdr': cdr}

def intern(env, atom_name, item):
    assert type(atom_name) is str
    env[atom_name] = item

def dispatch(fn_name, env, args):
    fn = dispatch_table.get(fn_name, None)
    if fn is not None:
        return fn(args)
    if fn_name in env:
        binding = env[fn_name]
        (maybe_list,
         ((maybe_atom, maybe_lambda),
          (maybe_list, arg_names),
          body)) = binding
        if (maybe_list != 'list' or
            maybe_atom != 'atom' or
            maybe_lambda != 'lambda'):
            raise Exception('Malformed function definition "%s"!'
                            % binding)
        new_env = env.copy()
        for i, x in enumerate(arg_names):
            intern(new_env, x[1], args[i])
        ret = None
        for form in body:
            ret = evalu(form, new_env)
        return ret  # Last result is returned
    raise Exception('Unknown function name: "%s"'
                    % fn_name)

def truthy(x):
    return x != ('bool', False)

def eval_atom(atom_name, env):
    if atom_name in dispatch_table.keys():
        return ('intproc', atom_name)
    elif atom_name in env:
        return env[atom_name]
    else:
        raise Exception("Unbound atom '%s'!" % atom_name)

def eval_list(l, env):
    # Empty list:
    if not l:
        return ('list', [])
    # Special forms:
    car = l[0]
    if car == ('atom', 'quote'):
        return l[1]
    if car == ('atom', 'cond'):
        clauses = l[1:]
        for clause in clauses:
            (maybe_list, clauselist) = clause
            if maybe_list != 'list':
                raise Exception('Cond clause "%s" not a list!"' %
                                l[1:])
            pred = clauselist[0]
            if (pred == ('atom', 'else') or
                evalu(pred, env) != ('bool', False)):
                return evalu(clauselist[1], env)
        return ('bool', True)
    # FIXME: cond should macroexpand to if or vice-versa?
    if car == ('atom', 'if'):
        pred = l[1]
        if truthy(evalu(pred, env)):
            return evalu(l[2], env)
        else:
            return evalu(l[3], env)
    elif car == ('atom', 'define'):
        typ, val = l[1]
        if typ == 'atom':
            intern(env, val, evalu(l[2], env))
            return ('nop', None)
        elif typ == 'list':
            fn_name, args = val[0], val[1:]
            lambd = ('list', [
                ('atom', 'lambda'),
                ('list', args),
                l[2:]])
            intern(env, val[0][1], lambd)
            return ('nop', None)
        else:
            raise Exception("Don't know how to bind '%s'!" % typ)
    elif car == ('atom', 'or'):
        for arg in l[1:]:
            ev = evalu(arg, env)
            if truthy(ev):
                return ev
        return ('bool', False)
    elif car == ('atom', 'and'):
        ev = None
        for arg in l[1:]:
            ev = evalu(arg, env)
            if not truthy(ev):
                return ('bool', False)
        if ev is None:
            return ('bool', True)
        else:
            return ev
    else:
        # Normal function application:
        return dispatch(car[1],
                        env,
                        [evalu(x, env) for x in l[1:]])

def evalu(ast, env):
    k, v = ast
    if k == 'int' or k == 'float' or k == 'bool':
        return ast
    if k == 'atom':
        return eval_atom(v, env)
    if k == 'list':
        return eval_list(v, env)
    raise Exception('evaluation error: "%s"' % str(ast))

def printable_value(ast):
    k, v = ast
    if k == 'int' or k == 'float':
        return str(v)
    if k == 'bool':
        return {True: "#t",
                False: "#f"}.get(v)
    if k == 'intproc':
        return "Internal procedure '%s'" % v
    if k == 'atom':
        return v
    if k == 'list':
        return '(' + ' '.join([printable_value(x)
                                for x in v]) + ')'
    if k == 'nop':
        return ''
    raise Exception('Unprintable ast "%s"' % str(ast))

def repl():
    env = {}
    while True:
        try:
            x = input("scheme> ").strip()
        except EOFError:
            print()
            break
        if x:
            try:
                pv = printable_value(evalu(parse_str(x), env))
                if pv:
                    print(pv)
            except Exception as e:
                print(e)

if __name__ == "__main__":
    repl()
