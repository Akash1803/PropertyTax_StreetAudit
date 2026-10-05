from streetaudit.ocr import decide_name, extract, normalize, same_road, street_number


def test_normalise_coimbatore_addresses():
    assert normalize("No. 94, C.S. RATHINASABAPATHI ST, Coimbatore - 641011") == "C.S. Rathinasabapathi Street"
    assert normalize("Coimbatore City Municipal Corporation  NSR ROAD") == "NSR Road"
    assert normalize("First Cross St") == "1st Cross Street"
    assert normalize("N.S.R. Rd") == "N.S.R. Road"


def test_numbered_streets_never_merge_and_pieces_do_not_match():
    assert same_road("Chinthamani Street 3", "Chinthamani St-3")
    assert not same_road("Chinthamani Street 3", "Chinthamani Street 4")
    assert not same_road("1st Main Road", "2nd Main Road")
    assert same_road("Ramasamy Street", "Ramaswamy Street")
    assert same_road("NSR Road", "N.S.R. Road")
    assert not same_road("Main Road", "NSR Main Road")          # a piece of a name is not the name
    assert street_number("Chinthamani St-3") == "3" and street_number("3rd Cross Street") == "3"


def test_extract_board_shop_tamil_and_google_panel():
    board = extract(["Coimbatore City Municipal Corporation", "NSR ROAD", "Ward 44"])
    assert board["kind"] == "street_name_board" and board["road_names"] == ["NSR Road"]
    shop = extract(["K.R. BAKES", "No. 12/3, N.S.R. Road, Saibaba Colony", "Coimbatore - 641 011", "Ph: 98765"])
    assert shop["kind"] == "shop_address"
    assert shop["doors"] == ["12/3"] and shop["pins"] == ["641011"]
    assert same_road(shop["road_names"][0], "NSR Road")          # the road comes before the locality
    assert "K.R. BAKES" in shop["shop_lines"]
    tamil = extract(["கோயம்புத்தூர் மாநகராட்சி", "சின்னம்மாள் தெரு"])
    assert tamil["kind"] == "street_name_board" and tamil["road_names"] == ["சின்னம்மாள் தெரு"]
    assert extract(["OPEN", "SALE 50%"])["road_names"] == []
    # a GST number or a phone number is not a door number
    gst = extract(["GST No: 33CHPPR0230R1ZV +91 95006 97959", "SAI NUTS & SPICES", "7/102 Ramsamy Street, Sai baba colony, Coimbatore - 641 038"])
    assert gst["doors"] == ["7/102"] and gst["pins"] == ["641038"] and gst["kind"] == "shop_address"
    assert extract(["Ph No 98765 43210", "Cell No: 9876543210"])["doors"] == []
    # Google's own labels are not signs on the building
    g = extract(["Search Google Maps", "Google Street View", "Dec 2022", "SAI NUTS & SPICES"],
                panel=["9 Ramasamy St", "Coimbatore, Tamil Nadu"])
    assert g["road_names"] == [] and g["google_address"] == "9 Ramasamy Street" and g["shop_lines"] == ["SAI NUTS & SPICES"]


def ev(name, kind, unit, dist=10):
    return {"name": name, "kind": kind, "unit_id": unit, "dist_m": dist}


def test_decision_rules():
    assert decide_name("NSR Road", [ev("N.S.R. Road", "street_name_board", "A")])["rule"] == "a"
    d = decide_name("Main_road", [ev("NSR Road", "street_name_board", "A")])
    assert d["rule"] == "b" and d["fix"] is True and d["name"] == "NSR Road"
    d = decide_name(None, [ev("Chinnammal Street", "shop_address", "A"), ev("Chinnamal St", "shop_address", "B"),
                           ev("Chinnammal Street", "shop_address", "B")])             # B votes once
    assert d["rule"] == "c" and d["name"] == "Chinnammal Street"
    assert decide_name(None, [ev("Chinnammal Street", "shop_address", "A")])["rule"] == "none"
    assert decide_name("Ramasamy Street", [ev("Chinnammal Street", "shop_address", "A")])["rule"] == "d"
    d = decide_name(None, [ev("Chinthamani Street 3", "street_name_board", "A"), ev("Chinthamani Street 4", "street_name_board", "B")])
    assert d["rule"] == "conflict"
