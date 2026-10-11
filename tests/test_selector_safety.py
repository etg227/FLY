"""Node-selection safety and fail-closed behaviour."""
import threading, unittest
from backend.mihomo_api import JapanNodeSelector, MihomoApiError, looks_like_japan

JP1="🇯🇵 Japan 01"; JP2="🇯🇵 Japan 02"; US="USA Los Angeles"

class FakeApi:
    def __init__(self, delays=None, current=JP1, switch_works=True):
        self.delays=delays or {}
        self.current=current
        self.switch_works=switch_works
        self.select_calls=[]
    def get_group(self,group="FLY-JP"):
        return {"all":[JP1,JP2,US],"now":self.current}
    def delay(self,node,url,timeout_ms=5000):
        value=self.delays.get((node,url),self.delays.get(node))
        if isinstance(value,Exception): raise value
        if value is None: raise MihomoApiError("timeout")
        return value
    def select(self,node,group="FLY-JP"):
        self.select_calls.append(node)
        if self.switch_works:self.current=node

class SelectorTests(unittest.TestCase):
    def test_service_failure_cannot_be_rescued_by_generic_connectivity(self):
        api=FakeApi({
            (JP1,"https://games.dmm.com/"):MihomoApiError("blocked"),
            (JP1,"https://www.gstatic.com/generate_204"):20,
        })
        sel=JapanNodeSelector(api,lambda m:None,
            required_urls=["https://games.dmm.com/","https://www.gstatic.com/generate_204"])
        with self.assertRaises(MihomoApiError):sel.measure(JP1)

    def test_all_failed_nodes_do_not_fallback_or_switch(self):
        api=FakeApi({JP1:MihomoApiError("down"),JP2:MihomoApiError("down")})
        sel=JapanNodeSelector(api,lambda m:None,required_urls=["https://svc.jp/"])
        with self.assertRaises(MihomoApiError):sel.auto_select(wait_s=1)
        self.assertEqual(api.select_calls,[])

    def test_excluded_failed_current_moves_to_healthy_backup(self):
        api=FakeApi({JP1:80,JP2:90},current=JP1)
        sel=JapanNodeSelector(api,lambda m:None,required_urls=["https://svc.jp/"])
        node,delay=sel.auto_select(wait_s=1,exclude={JP1})
        self.assertEqual((node,delay),(JP2,90))
        self.assertEqual(api.current,JP2)

    def test_cycle_next_tests_before_switch_and_skips_dead_node(self):
        api=FakeApi({JP1:70,JP2:MihomoApiError("down")},current=JP1)
        sel=JapanNodeSelector(api,lambda m:None,required_urls=["https://svc.jp/"])
        with self.assertRaises(MihomoApiError):sel.cycle_next()
        self.assertEqual(api.current,JP1)
        self.assertEqual(api.select_calls,[])

    def test_switch_result_is_verified(self):
        api=FakeApi({JP1:70,JP2:80},current=JP1,switch_works=False)
        sel=JapanNodeSelector(api,lambda m:None,required_urls=["https://svc.jp/"])
        with self.assertRaises(MihomoApiError):sel.cycle_next()
        self.assertEqual(api.current,JP1)

    def test_custom_site_probe_failure_does_not_block_start(self):
        # A user-added homepage that ignores probes must not make every node
        # "unhealthy" and refuse to start; required targets still decide health.
        api=FakeApi({(JP1,"https://www.gstatic.com/generate_204"):40,
                     (JP2,"https://www.gstatic.com/generate_204"):60,
                     (JP1,"http://friend.jp/"):MihomoApiError("timeout"),
                     (JP2,"http://friend.jp/"):MihomoApiError("timeout")})
        sel=JapanNodeSelector(api,lambda m:None,required_urls=[],optional_urls=["http://friend.jp/"])
        self.assertEqual(sel.auto_select(wait_s=1),(JP1,40))
        self.assertEqual(sel.measure(JP1),40)

    def test_node_passing_custom_site_probe_is_preferred(self):
        api=FakeApi({(JP1,"https://svc.jp/"):30,(JP2,"https://svc.jp/"):70,
                     (JP1,"http://friend.jp/"):MihomoApiError("timeout"),
                     (JP2,"http://friend.jp/"):90})
        sel=JapanNodeSelector(api,lambda m:None,required_urls=["https://svc.jp/"],
                              optional_urls=["http://friend.jp/"])
        self.assertEqual(sel.auto_select(wait_s=1),(JP2,90))

    def test_custom_site_probe_cannot_rescue_failed_required_target(self):
        api=FakeApi({(JP1,"https://svc.jp/"):MihomoApiError("blocked"),(JP1,"http://friend.jp/"):20})
        sel=JapanNodeSelector(api,lambda m:None,required_urls=["https://svc.jp/"],
                              optional_urls=["http://friend.jp/"])
        with self.assertRaises(MihomoApiError):sel.measure(JP1)

    def test_sticky_node_missing_custom_site_is_reselected(self):
        api=FakeApi({(JP1,"https://svc.jp/"):30,(JP2,"https://svc.jp/"):50,
                     (JP1,"http://friend.jp/"):MihomoApiError("timeout"),(JP2,"http://friend.jp/"):60})
        logs=[]
        sel=JapanNodeSelector(api,logs.append,required_urls=["https://svc.jp/"],
                              optional_urls=["http://friend.jp/"])
        self.assertEqual(sel.auto_select(wait_s=1,preferred=JP1),(JP2,60))
        self.assertFalse(any("degraded" in x for x in logs))

    def test_one_character_keyword_cannot_match_us_node(self):
        self.assertFalse(looks_like_japan(US,["a"]))
        self.assertTrue(looks_like_japan(JP1,["Japan"]))

    def test_selection_can_be_cancelled(self):
        ev=threading.Event(); ev.set()
        api=FakeApi({JP1:50})
        sel=JapanNodeSelector(api,lambda m:None,required_urls=["https://svc.jp/"],cancel_event=ev)
        with self.assertRaises(MihomoApiError):sel.auto_select(wait_s=1)

if __name__=="__main__":unittest.main()
