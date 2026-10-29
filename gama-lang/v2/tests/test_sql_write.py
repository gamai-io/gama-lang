# -*- coding: utf-8 -*-
"""Tests for the guarded sql_write connector (INSERT/UPDATE/DELETE only)."""
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G


class SqlWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "w.db")
        con = sqlite3.connect(self.db)
        con.execute("create table t(a int)")
        con.commit()
        con.close()
        self._old_db = os.environ.get("GAMA_L_DB")
        self._old_allow = os.environ.get("GAMA_L_ALLOW_WRITE")
        os.environ["GAMA_L_DB"] = self.db

    def tearDown(self):
        for key, old in (("GAMA_L_DB", self._old_db),
                         ("GAMA_L_ALLOW_WRITE", self._old_allow)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def rows(self):
        con = sqlite3.connect(self.db)
        try:
            return [r[0] for r in con.execute("select a from t order by a")]
        finally:
            con.close()

    def test_write_inserts_row_via_function(self):
        os.environ["GAMA_L_ALLOW_WRITE"] = "1"
        it = G.run(
            'fn up() -> Bool effects {sql.write} {\n'
            '  return sql_write("insert into t(a) values (7)")\n'
            '}\n'
            'print up()\n'
        )
        self.assertEqual(it.out, ["True"])
        self.assertEqual(self.rows(), [7])

    def test_write_update_and_delete(self):
        os.environ["GAMA_L_ALLOW_WRITE"] = "1"
        G.run('let ok = sql_write("insert into t(a) values (1)")\n')
        G.run('let ok = sql_write("update t set a = 2 where a = 1;")\n')
        self.assertEqual(self.rows(), [2])
        G.run('let ok = sql_write("delete from t where a = 2")\n')
        self.assertEqual(self.rows(), [])

    def test_writes_disabled_without_flag(self):
        os.environ.pop("GAMA_L_ALLOW_WRITE", None)
        with self.assertRaises(G.GamaRuntimeError) as ctx:
            G.run('let ok = sql_write("insert into t(a) values (7)")\n')
        self.assertIn("writes disabled", str(ctx.exception).lower())
        self.assertEqual(self.rows(), [])

    def test_missing_db_is_error(self):
        os.environ.pop("GAMA_L_DB", None)
        os.environ["GAMA_L_ALLOW_WRITE"] = "1"
        with self.assertRaises(G.GamaRuntimeError) as ctx:
            G.run('let ok = sql_write("insert into t(a) values (7)")\n')
        self.assertIn("GAMA_L_DB", str(ctx.exception))

    def test_ddl_is_rejected(self):
        os.environ["GAMA_L_ALLOW_WRITE"] = "1"
        for stmt in ("drop table t", "alter table t add column b int",
                     "create table u(a int)", "pragma table_info(t)",
                     "attach database 'x.db' as x"):
            with self.subTest(stmt=stmt):
                with self.assertRaises(G.GamaRuntimeError):
                    G.run('let ok = sql_write("%s")\n' % stmt)
        self.assertEqual(self.rows(), [])

    def test_select_via_write_is_rejected(self):
        os.environ["GAMA_L_ALLOW_WRITE"] = "1"
        with self.assertRaises(G.GamaRuntimeError):
            G.run('let ok = sql_write("select a from t")\n')

    def test_multiple_statements_are_rejected(self):
        os.environ["GAMA_L_ALLOW_WRITE"] = "1"
        stmt = "insert into t(a) values (7); delete from t"
        with self.assertRaises(G.GamaRuntimeError) as ctx:
            G.run('let ok = sql_write("%s")\n' % stmt)
        self.assertIn("one statement", str(ctx.exception).lower())
        self.assertEqual(self.rows(), [])

    def test_requires_sql_write_effect(self):
        os.environ["GAMA_L_ALLOW_WRITE"] = "1"
        with self.assertRaises(G.GamaEffectError):
            G.check(
                'fn up() -> Bool effects {} {\n'
                '  return sql_write("insert into t(a) values (7)")\n'
                '}\n'
                'print up()\n'
            )


if __name__ == "__main__":
    unittest.main()
