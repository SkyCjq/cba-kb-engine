from pathlib import Path
import pytest
from cba_kb.aliases import Clubs, UnresolvedClub

ROOT = Path(__file__).resolve().parents[1]

FOREIGN_2024_2025 = {
    '宁波町渥': 'ningbo_fubang', '青岛国信制药': 'qingdao_guoxin_haitian',
    '深圳马可波罗': 'shenzhen_xinshiji', '浙江方兴渡': 'zhejiang_guangsha',
    '天津先行者': 'tianjin_ronggang', '浙江稠州金租': 'zhejiang_chouzhou',
    '山西汾酒': 'shanxi_fenjiu', '北京北汽': 'beijing_shougang',
    '新疆伊力特': 'xinjiang_guanghui', '四川丰谷酒业': 'sichuan_jincheng',
    '广东东阳光': 'guangdong_hongyuan', '山东高速': 'shandong_gaosu',
    '福建晋江文旅': 'fujian_xunxing', '上海久事': 'shanghai_jiushi',
    '辽宁本钢': 'liaoning_shenyang_sansheng', '南京头排苏酒': 'nanjing_tongxi',
    '北京控股': 'beijing_konggu', '广州朗肽海本': 'guangzhou_longshi',
    '九台农商银行': 'jilin_jiutai', '江苏肯帝亚': 'jiangsu_kendiya'}


def test_every_registered_sponsor_resolves_exactly():
    clubs = Clubs(ROOT)
    for name, club_id in FOREIGN_2024_2025.items():
        assert clubs.resolve(name, '2024-2025') == club_id
    assert len(set(FOREIGN_2024_2025.values())) == 20


def test_domestic_and_sponsor_names_are_season_scoped():
    clubs = Clubs(ROOT)
    assert clubs.resolve('北京首钢') == 'beijing_shougang'
    assert clubs.resolve('龙狮', '2024-2025') == 'guangzhou_longshi'
    with pytest.raises(UnresolvedClub):
        clubs.resolve('北京北汽', '2025-2026')


def test_unknown_name_fails_closed_or_is_reported():
    with pytest.raises(UnresolvedClub, match='Unknown club name'):
        Clubs(ROOT).resolve('中央陆军')
    lenient = Clubs(ROOT, strict=False)
    assert lenient.resolve('中央陆军', '2024-2025') is None
    assert lenient.report()['unresolved'] == [{'name': '中央陆军', 'season': '2024-2025'}]


def test_whitespace_and_width_normalization():
    clubs = Clubs(ROOT)
    assert clubs.resolve('  宁波町渥\u3000', '2024-2025') == 'ningbo_fubang'
    assert clubs.resolve('四川', '2024-2025') == 'sichuan_jincheng'
    with pytest.raises(UnresolvedClub):
        clubs.resolve('   ')


def test_bayi_is_defunct_history_not_a_free_alias():
    clubs = Clubs(ROOT)
    assert clubs.resolve('八一富邦', '2019-2020') == 'bayi'
    with pytest.raises(UnresolvedClub, match='defunct'):
        clubs.resolve('八一', '2020-2021')
    assert clubs.resolve('八一', '2020-2021', role='event') == 'bayi'


def test_source_typo_is_explicit_and_season_scoped():
    clubs = Clubs(ROOT)
    assert clubs.resolve('广州朗钛海本', '2024-2025') == 'guangzhou_longshi'
    config = clubs.clubs['guangzhou_longshi']
    assert '广州朗钛海本' not in config['official_foreign_sponsor_by_season']['2024-2025']
    assert config['source_typo_variant']['2024-2025'] == ['广州朗钛海本']
    with pytest.raises(UnresolvedClub):
        clubs.resolve('广州朗钛海本', '2025-2026')
