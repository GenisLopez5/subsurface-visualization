using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.UIElements;
using Subsurface.Borehole;
using Subsurface.Lithology;
using Object=UnityEngine.Object;

public static class VerifyBorehole
{
    static BoreholeViewer Viewer=>Object.FindAnyObjectByType<BoreholeViewer>();
    static void Require(bool ok,string message){if(!ok)throw new InvalidOperationException(message);}
    // The current UITK Clickable exposes this to its own keyboard handler internally.
    // Test harness only: invoke the same handler without relying on native focus.
    static void Submit(Button button){typeof(Clickable).GetMethod("SimulateSingleClick",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).Invoke(button.clickable,new object[]{null,0});}
    public static async Task<object> Run()
    {
        var v=Viewer;Require(v!=null&&v.column.Document!=null,"No live borehole document");
        int before=v.AdvancingFrames;await Task.Delay(350);Require(v.AdvancingFrames>before,"The player loop is frozen");
        Require(v.column.Document.SourceIntervalCount==17&&v.column.Document.TopFeet==25400&&v.column.Document.BottomFeet==25700,"Incorrect sample data");
        var viewport=v.Root.Q("viewport");double depth=v.FocusDepthFeet;
        using(var wheel=WheelEvent.GetPooled(new Event{type=EventType.ScrollWheel,delta=new Vector2(0,3),mousePosition=viewport.worldBound.center}))viewport.SendEvent(wheel);
        await Task.Delay(100);bool wheelWorks=v.FocusDepthFeet>depth;Require(wheelWorks,"Wheel event did not move through depth");
        var rotation=v.columnCamera.transform.rotation;var center=viewport.worldBound.center;
        using(var down=PointerDownEvent.GetPooled(new Event{type=EventType.MouseDown,button=0,mousePosition=center}))viewport.SendEvent(down);
        using(var move=PointerMoveEvent.GetPooled(new Event{type=EventType.MouseDrag,button=0,mousePosition=center+new Vector2(45,0)}))viewport.SendEvent(move);
        using(var up=PointerUpEvent.GetPooled(new Event{type=EventType.MouseUp,button=0,mousePosition=center+new Vector2(45,0)}))viewport.SendEvent(up);
        await Task.Delay(100);bool dragWorks=Quaternion.Angle(rotation,v.columnCamera.transform.rotation)>.1f;Require(dragWorks,"Drag events did not rotate the camera");
        double window=v.WindowFeet;Submit(v.Root.Q<Button>("zoomIn"));await Task.Delay(100);bool zoomWorks=v.WindowFeet<window;Require(zoomWorks,"Zoom button did not change the depth window");
        v.SelectInterval(5,true);await Task.Delay(100);Physics.SyncTransforms();
        var row=v.column.Document.Intervals[5];var position=v.column.transform.TransformPoint(new Vector3(0,v.column.DepthMap.ToY((row.TopFeet+row.BottomFeet)*.5),0));var screen=v.columnCamera.WorldToViewportPoint(position);
        var hit=v.column.Pick(v.columnCamera.ViewportPointToRay(screen));Require(hit==5,"Cylinder picking returned the wrong interval: "+hit);
        var click=viewport.worldBound.position+new Vector2(screen.x*viewport.contentRect.width,(1-screen.y)*viewport.contentRect.height);v.SelectInterval(0,false);
        using(var down=PointerDownEvent.GetPooled(new Event{type=EventType.MouseDown,button=0,mousePosition=click}))viewport.SendEvent(down);
        using(var up=PointerUpEvent.GetPooled(new Event{type=EventType.MouseUp,button=0,mousePosition=click}))viewport.SendEvent(up);
        bool clickWorks=v.column.SelectedIndex==5;Require(clickWorks,"Pointer click did not select the cylinder interval");
        v.Root.Q<DropdownField>("appearance").value="Legend / patterns";Require(v.column.Surfaces.All(s=>s.displayMode==DisplayMode.Legend),"Legend switch failed");v.Root.Q<DropdownField>("appearance").value="Textured lithology";
        var previous=v.column.Document;Require(!v.LoadCsvText("top_depth,bottom_depth,lithology\n0,20,Shale\n10,30,Sandstone","Invalid.csv")&&ReferenceEquals(previous,v.column.Document),"Invalid CSV replaced the valid document");
        var picker=DebugCsvFileDialog.EditorPicker;bool pickerInvoked=false;
        try{DebugCsvFileDialog.EditorPicker=()=>{pickerInvoked=true;return Path.GetFullPath("Assets/Borehole/Samples/example.csv");};Submit(v.Root.Q<Button>("loadCsv"));await Task.Delay(100);Require(pickerInvoked&&v.LastError==null&&v.column.Document.SourceIntervalCount==17,"Debug CSV button did not load through the picker adapter");}
        finally{DebugCsvFileDialog.EditorPicker=picker;}
        bool gaps=v.LoadCsvText("top_depth,bottom_depth,lithology\n100,120,Shale\n130,150,Sandstone\n150,170,Unmapped fixture","Gap check.csv");Require(gaps&&v.column.Document.Intervals.Count==4&&v.column.Document.Intervals[1].IsGap,"Gap did not create a no-data section");Require(v.column.Surfaces[1].rawLabel=="Not Available","Gap material is incorrect");
        var image=new Texture2D(16,32);var pixels=Enumerable.Repeat(new Color(.6f,.7f,.8f),16*32).ToArray();image.SetPixels(pixels);image.Apply();
        try
        {
            Require(v.SetAnalysisResult(File.ReadAllText("Assets/Borehole/Samples/example.csv"),image,"Backend image fixture"),"Backend result API failed");v.SetDepth(25580);double saved=v.FocusDepthFeet;
            Submit(v.Root.Q<Button>("viewDocument"));Require(v.DocumentMode&&v.SegmentedImage==image&&v.Root.Q<Image>("documentImage").image==image,"Segmented document view failed");Submit(v.Root.Q<Button>("view3d"));Require(!v.DocumentMode&&Math.Abs(v.FocusDepthFeet-saved)<.001,"Switching views lost the depth position");
            v.LoadCsvText(File.ReadAllText("Assets/Borehole/Samples/example.csv"),"example.csv");Require(v.SegmentedImage==null,"Loading another CSV retained the stale document image");
        }
        finally{Object.Destroy(image);}
        v.SelectInterval(5,true);await Task.Delay(150);
        var result=new{passed=true,initialFrame=before,finalFrame=v.AdvancingFrames,sourceIntervals=17,topFeet=25400,bottomFeet=25700,wheelWorks,dragWorks,zoomWorks,clickWorks,pickerAdapterWorks=pickerInvoked,gapWorks=gaps,invalidImportRetainsPrevious=true,documentImageApiWorks=true,viewSwitchPreservesDepth=true,staleImageCleared=true};
        Directory.CreateDirectory("Assets/Borehole/Generated/Verification");File.WriteAllText("Assets/Borehole/Generated/Verification/LiveResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result,Newtonsoft.Json.Formatting.Indented));return result;
    }
    public static void ShowGaps(){Viewer.LoadCsvText("top_depth,bottom_depth,lithology\n100,120,Shale\n130,150,Sandstone\n150,170,Unmapped fixture","Gap / unknown-label check.csv");Viewer.FitColumn();Viewer.SelectInterval(1,false);}
    public static void ShowSample(){Viewer.LoadCsvText(File.ReadAllText("Assets/Borehole/Samples/example.csv"),"example.csv");Viewer.SelectInterval(5,true);}
    public static void ShowDocument(){Viewer.SetDocumentMode(true);}
    public static object CheckCleanup()
    {
        int textures=Resources.FindObjectsOfTypeAll<RenderTexture>().Count(t=>t.name=="Borehole viewport");int meshes=Resources.FindObjectsOfTypeAll<Mesh>().Count(m=>m.name=="Shared capped borehole cylinder");int materials=Resources.FindObjectsOfTypeAll<Material>().Count(m=>m.name=="Borehole selection");
        var result=new{playing=Application.isPlaying,ownedViewTextures=textures,ownedCylinderMeshes=meshes,ownedSelectionMaterials=materials};
        File.WriteAllText("Assets/Borehole/Generated/Verification/CleanupResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result,Newtonsoft.Json.Formatting.Indented));Require(!Application.isPlaying&&textures==0&&meshes==0&&materials==0,"Owned borehole resources survived Play-mode exit");return result;
    }
}
