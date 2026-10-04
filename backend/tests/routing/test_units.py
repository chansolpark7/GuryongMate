"""한국어 안내 문구·경로 모양 복원·교차 판정 단위 테스트."""
import pytest

from app.routing import narrative
from app.routing.hazards import contains, line_hits
from app.routing.valhalla import decode_polyline6
from tests.routing.test_api import encode6


@pytest.mark.parametrize("word,ro,eul", [
    ("구룡포길", "구룡포길로", "구룡포길을"),      # ㄹ 받침 -> 로
    ("호미로", "호미로로", "호미로를"),            # 받침 없음
    ("구룡포항", "구룡포항으로", "구룡포항을"),    # 받침 있음
    ("Main St", "Main St로", "Main St를"),        # 한글이 아니면 받침 없음으로 본다
])
def test_josa(word, ro, eul):
    assert narrative.ro(word) == ro and narrative.eul(word) == eul


@pytest.mark.parametrize("meters,text", [(3, "10m"), (124, "120m"), (999, "1000m"), (1000, "1.0km"), (2350, "2.4km")])
def test_distance(meters, text):
    assert narrative.fmt_distance(meters) == text


@pytest.mark.parametrize("m,kind,text", [
    ({"type": 1, "street_names": ["구룡포길"], "length": 0.12}, "start", "구룡포길로 출발해 120m 이동"),
    ({"type": 8, "street_names": ["호미로"], "length": 0.3}, "straight", "호미로를 따라 300m 직진"),
    ({"type": 15, "length": 0.05}, "left", "좌회전 후 50m 이동"),
    ({"type": 10, "street_names": ["용주로7번길"], "length": 0.03}, "right", "용주로7번길로 우회전 후 30m 이동"),
    ({"type": 40, "length": 0.04}, "steps", "계단으로 40m 이동"),
    ({"type": 26, "roundabout_exit_count": 2, "length": 0.1}, "roundabout", "회전교차로에서 2번째 출구"),
    ({"type": 6, "length": 0}, "arrive", "왼쪽에 목적지"),
    ({"type": 99, "length": 0.2}, "other", "200m 이동"),
])
def test_describe(m, kind, text):
    assert narrative.describe(m) == (kind, text)


def test_polyline_roundtrip():
    coords = [(129.555641, 35.989305), (129.560455, 35.990620), (129.561799, 35.992740), (129.4, 35.8)]
    assert decode_polyline6(encode6(coords)) == pytest.approx(coords)


SQUARE = ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))


def test_contains():
    assert contains(SQUARE, (0.5, 0.5)) and not contains(SQUARE, (1.5, 0.5))


def test_line_hits_even_without_vertex_inside():
    assert line_hits([(-1, 0.5), (2, 0.5)], SQUARE)          # 양 끝이 밖이어도 가로지르면 지난 것
    assert line_hits([(-1, -1), (0.5, 0.5)], SQUARE)          # 끝점이 안
    assert not line_hits([(-1, 2), (2, 2), (2, -1)], SQUARE)  # 바깥으로 돌아감
