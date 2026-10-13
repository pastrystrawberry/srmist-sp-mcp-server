from srmist_portal.attendance import calculate_margin, compute_subject_margins
from srmist_portal.portal_client import extract_tables


def test_attendance_margin_basic():
    result = calculate_margin(attended=18, total=20, target_percent=75)
    assert result["current_attendance_percent"] == 90.0
    assert result["additional_absence_units_allowed_at_or_above_target"] == 4
    assert result["consecutive_attendance_units_to_reach_target"] == 0


def test_classes_needed_to_reach_target():
    result = calculate_margin(attended=6, total=10, target_percent=75)
    assert result["current_attendance_percent"] == 60.0
    assert result["consecutive_attendance_units_to_reach_target"] == 6


def test_impossible_finite_target_of_100_if_absences_exist():
    result = calculate_margin(attended=8, total=10, target_percent=100)
    assert result["consecutive_attendance_units_to_reach_target"] is None
    assert result["additional_absence_units_allowed_at_or_above_target"] == 0


def test_reject_invalid_target():
    try:
        calculate_margin(attended=4, total=5, target_percent=0)
    except ValueError:
        pass
    else:
        raise AssertionError("target 0 should be rejected")


def test_html_table_parsing():
    html = """<html><body><h2>Course Wise Attendance</h2><table>
    <tr><th>Course Code</th><th>Max. Hours</th><th>Att. Hours</th><th>Total Percentage</th></tr>
    <tr><td>CS101</td><td>20</td><td>18</td><td>90.00</td></tr>
    </table></body></html>"""
    tables = extract_tables(html)
    assert len(tables) == 1
    assert tables[0]["headers"] == ["Course Code", "Max. Hours", "Att. Hours", "Total Percentage"]
    assert tables[0]["rows"][0]["Course Code"] == "CS101"
    result = compute_subject_margins(tables, 75)
    assert result["ok"] is True
    assert result["subjects"][0]["additional_absence_units_allowed_at_or_above_target"] == 4


def test_hour_columns_are_reported_as_hours_not_classes():
    tables = [{
        "headers": ["Course Code", "Max. Hours", "Att. Hours"],
        "rows": [{"Course Code": "CS101", "Max. Hours": "20", "Att. Hours": "18"}],
    }]
    result = compute_subject_margins(tables, 75)
    assert result["ok"]
    record = result["subjects"][0]
    assert record["count_unit"] == "hours"
    assert record["additional_absence_units_allowed_at_or_above_target"] == 4
