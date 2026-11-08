from PIL import Image

from bbox_detector.legend_multiview import merge_edge_votes, refine_legend_multiview
from bbox_detector.legend_pipeline import LegendPolicy, apply_legend_policy


def candidate(view, box, visible=(True,True,True,True)):
    return {"view_id":view,"box":box,"confidence":.8,"edge_visible":list(visible)}


def test_repeated_boxes_from_one_crop_cannot_expand_an_edge():
    seed=[20,50,150,100]
    boxes=[candidate(0,[20,50,190+i,100]) for i in range(4)]
    merged,details=merge_edge_votes(seed,boxes,width=220)
    assert merged==seed
    assert details["reason"]=="insufficient_independent_votes"


def test_two_views_can_expand_a_supported_edge_without_shrinking_seed():
    seed=[20,50,150,100]
    merged,details=merge_edge_votes(seed,[candidate(0,[25,51,190,99]),candidate(1,[25,51,192,99])],width=220)
    assert merged[:2]==seed[:2] and merged[3]==100
    assert merged[2]==191
    assert details["edge_votes"]["xmax"]["support_views"]==2


def test_internal_crop_boundary_cannot_vote_for_document_edge():
    seed=[20,50,150,100]
    boxes=[candidate(i,[20,50,190,100],(True,True,False,True)) for i in range(2)]
    assert merge_edge_votes(seed,boxes,width=220)[0]==seed


def test_other_rows_disconnected_regions_and_excessive_expansion_are_rejected():
    seed=[20,50,150,100]
    wrong_row=[candidate(i,[20,150,190,200]) for i in range(2)]
    far=[candidate(i,[250,50,350,100]) for i in range(2)]
    huge=[candidate(i,[90,50,350,100]) for i in range(2)]
    assert merge_edge_votes(seed,wrong_row,width=400)[0]==seed
    assert merge_edge_votes(seed,far,width=400)[0]==seed
    merged,details=merge_edge_votes(seed,huge,width=400)
    assert merged==seed and details["reason"]=="excessive_expansion"


def test_five_views_preserve_seed_when_detector_finds_nothing(tmp_path):
    path=tmp_path/"scan.tif"
    Image.new("RGB",(200,400),"white").save(path)
    class Detector:
        def __init__(self):self.sizes=[]
        def predict(self,image,**kwargs):
            self.sizes.append(image.size)
            return iter(())
    detector=Detector()
    seed=[10,50,180,90]
    details={}
    assert refine_legend_multiview(path,seed,column_top=180,detector=detector,imgsz=64,diagnostics=details)==seed
    assert len(detector.sizes)==5
    assert detector.sizes[:2]==[(200,120),(200,60)]
    assert len(details["views"])==5


def test_verified_frame_skips_retry_and_preserves_both_columns():
    base={"image_name":"scan.tif","width":200,"height":400,"bounding_boxes":{
        "depth_column":[10,180,20,390],"interpreted_lithology_column":[100,190,120,395],
        "lithology_legend":[10,50,180,90],
    }}
    def frame(path,seed,*,column_top,diagnostics):
        diagnostics["reason"]="supported_frame"
        return list(seed)
    result=apply_legend_policy("not-read.tif",base,LegendPolicy(strategy="fragments_frame",multiview_retry=True),
                               candidate_details={"groups":[]},frame_refiner=frame)
    assert result["bounding_boxes"]==base["bounding_boxes"]
