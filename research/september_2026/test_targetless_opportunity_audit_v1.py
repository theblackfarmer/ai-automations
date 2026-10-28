"""Synthetic invariants only; no QuantConnect cloud run or historical data."""
import copy
import unittest
from targetless_opportunity_audit_v1 import audit, classify


def base():
    return dict(observation_id="r1", parent_id="p1", formation_id="f1", decision_at=600,
                side="LONG", entry_reference=100.0, exact_far_wick_stop=96.0,
                parent_state="ACTIVE", coverage="COMPLETE", m1_state="ALIGNED", m5_state="ALIGNED",
                canonical_target_available=False, knowledge_times={"parent":300,"m1":480,"m5":600,"formation":420,"return":600},
                candidate_zones=[])


def zone(**updates):
    z=dict(id="z1",kind="SUPPLY",known_at=480,status_known_at=540,state="VALID",
           owner_relation="SELECTED_PARENT",timeframe_minutes=15,low=110.0,high=113.0,coverage="COMPLETE")
    z.update(updates)
    return z


class TargetlessTests(unittest.TestCase):
    def test_source_has_no_target_and_empty_catalog(self):
        r=classify(base())
        self.assertEqual(r["analysis_state"],"TARGETLESS_WITHOUT_PROVEN_OBJECTIVE")
        self.assertIn("NO_CATALOG_ZONES",r["reasons"])
        self.assertFalse(r["execution_allowed"])

    def test_shadow_is_not_tradable(self):
        r=base(); r["candidate_zones"]=[zone()]
        v=classify(r)
        self.assertEqual(v["analysis_state"],"TARGETLESS_POSSIBLE_ZONE_MAPPING_DEFECT")
        self.assertEqual(v["shadow_candidates"][0]["proximal"],110.0)
        self.assertFalse(v["execution_allowed"])
        self.assertTrue(v["source_audit_required"])

    def test_future_target_or_status_never_available(self):
        for change in ({"known_at":601},{"status_known_at":601}):
            with self.subTest(change=change):
                r=base();r["candidate_zones"]=[zone(**change)]
                self.assertEqual(classify(r)["shadow_candidates"],[])

    def test_used_or_wrong_target_never_proposed(self):
        for change in ({"state":"CONSUMED"},{"owner_relation":"OTHER"},{"kind":"DEMAND"},
                       {"low":99.0},{"coverage":"UNKNOWN"}):
            with self.subTest(change=change):
                r=base();r["candidate_zones"]=[zone(**change)]
                self.assertEqual(classify(r)["shadow_candidates"],[])

    def test_sweep_reclaim_is_hypothesis_not_fill(self):
        r=base();r["candidate_zones"]=[zone(state="SWEPT_RECLAIM")]
        v=classify(r)
        self.assertEqual(len(v["shadow_candidates"]),1)
        self.assertFalse(v["execution_allowed"])

    def test_unconfirmed_structure_or_parent_never_upgraded(self):
        for change in ({"m1_state":"OPPOSED"},{"m5_state":"UNKNOWN"},
                       {"parent_state":"INVALID"},{"coverage":"UNKNOWN"}):
            with self.subTest(change=change):
                r=base();r.update(change);r["candidate_zones"]=[zone()]
                self.assertEqual(classify(r)["analysis_state"],"INELIGIBLE_OR_UNKNOWN_CONTEXT")

    def test_missing_required_knowledge_cannot_pass(self):
        r=base();r["knowledge_times"].pop("formation")
        self.assertFalse(classify(r)["core_ready"])
        self.assertIn("KNOWLEDGE_UNKNOWN",classify(r)["reasons"])

    def test_late_required_fact_rejected(self):
        r=base();r["knowledge_times"]["m1"]=601
        with self.assertRaisesRegex(ValueError,"after decision"):
            classify(r)

    def test_short_geometries_and_stop(self):
        r=base();r.update(side="SHORT",entry_reference=100.,exact_far_wick_stop=105.)
        r["candidate_zones"]=[zone(kind="DEMAND",low=85.,high=90.)]
        self.assertEqual(len(classify(r)["shadow_candidates"]),1)
        r["exact_far_wick_stop"]=99
        with self.assertRaisesRegex(ValueError,"adverse"):
            classify(r)

    def test_no_time_travel_in_later_return_family(self):
        r=base();second=copy.deepcopy(r);second["observation_id"]="r2";second["decision_at"]=660
        second["canonical_target_available"]=True
        a=audit([second,r],expected_total=2,expected_missing=1)
        self.assertTrue(a["summary"]["source_complete_for_expected_population"])
        self.assertEqual(a["observations"][0]["return_number_within_family"],1)
        self.assertEqual(a["observations"][0]["canonical_target_available"],False)
        self.assertEqual(a["observations"][1]["analysis_state"],"BASELINE_SOURCE_READY_TARGET_RECORDED")
        self.assertEqual(a["summary"]["later_return_state_transitions"][0]["count"],1)

    def test_same_minute_different_formation_not_deduplicated(self):
        r=base();other=copy.deepcopy(r);other.update(observation_id="r2",formation_id="f2")
        a=audit([r,other])
        self.assertEqual(a["summary"]["distinct_parent_formation_side_families"],2)
        self.assertEqual(a["summary"]["families_with_later_returns"],0)

    def test_dont_fake_population_without_rows(self):
        with self.assertRaisesRegex(ValueError,"no actual"):
            audit([])

    def test_incomplete_population_never_claimed_complete(self):
        a=audit([base()],expected_total=3069,expected_missing=2054)
        self.assertFalse(a["summary"]["source_complete_for_expected_population"])


if __name__ == "__main__":
    unittest.main()
