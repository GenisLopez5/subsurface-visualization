using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.UIElements;
using Subsurface.Borehole;
using Subsurface.Lithology;
using Object=UnityEngine.Object;

public static class VerifySynchronizedViewer
{
    static BoreholeViewer Viewer=>Object.FindAnyObjectByType<BoreholeViewer>();
    static void Require(bool value,string message){if(!value)throw new InvalidOperationException(message);}
    static void Submit(Button button)=>typeof(Clickable).GetMethod("SimulateSingleClick",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).Invoke(button.clickable,new object[]{null,0});
    static void Wheel(VisualElement target,float amount){using(var e=WheelEvent.GetPooled(new Event{type=EventType.ScrollWheel,delta=new Vector2(0,amount),mousePosition=target.worldBound.center}))target.SendEvent(e);}
    static void Drag(VisualElement target,Vector2 delta)
    {
        var center=target.worldBound.center;
        using(var e=PointerDownEvent.GetPooled(new Event{type=EventType.MouseDown,button=0,mousePosition=center}))target.SendEvent(e);
        using(var e=PointerMoveEvent.GetPooled(new Event{type=EventType.MouseDrag,button=0,mousePosition=center+delta}))target.SendEvent(e);
        using(var e=PointerUpEvent.GetPooled(new Event{type=EventType.MouseUp,button=0,mousePosition=center+delta}))target.SendEvent(e);
    }
    const string Csv="top_depth,bottom_depth,lithology,top_y,bottom_y\n1000,1100,Shale,800,1800\n1100,1150,Sandstone,1800,2300\n1160,1250,Limestone,2500,5200\n1250,1300,Unmapped fixture,5200,5600";
    public static Texture2D FixtureImage()
    {
        var texture=new Texture2D(400,3000,TextureFormat.RGBA32,false){name="Synchronization QA fixture",hideFlags=HideFlags.DontSave};var pixels=new Color32[400*3000];
        for(int y=0;y<3000;y++)for(int x=0;x<400;x++)
        {
            int sourceY=5998-y*2;Color32 color=new Color32(250,248,244,255);
            if(x%40==0||sourceY%100<2)color=new Color32(203,198,210,255);
            if(x>=90&&x<130){color=sourceY<1800?new Color32(91,96,105,255):sourceY<2300?new Color32(210,175,97,255):sourceY<2500?new Color32(190,190,190,255):new Color32(131,157,159,255);if((x+y)%19<2)color=new Color32(66,70,80,255);}
            if(x>140&&x<290&&Math.Abs(x-(210+Math.Sin(sourceY*.01)*40))<2)color=new Color32(54,154,105,255);
            if(sourceY==800||sourceY==1800||sourceY==2300||sourceY==2500||sourceY==5200||sourceY==5600)color=new Color32(79,0,181,255);
            pixels[y*400+x]=color;
        }
        texture.SetPixels32(pixels);texture.Apply();return texture;
    }
    static Texture2D qaImage;
    public static void ShowFixture()
    {
        foreach(var old in Resources.FindObjectsOfTypeAll<Texture2D>().Where(t=>t.name=="Synchronization QA fixture"))Object.Destroy(old);qaImage=FixtureImage();Require(Viewer.SetAnalysisResult(Csv,qaImage,"Synchronization QA fixture · original 800 × 6000 px",800,6000),"API fixture failed");Viewer.Zoom(.23);Viewer.SetDepth(1124);
    }
    public static void ShowProvided()
    {
        Viewer.SetAnalysisResult(Viewer.initialCsv.text,Viewer.initialDocumentImage,Viewer.initialSourceName);foreach(var old in Resources.FindObjectsOfTypeAll<Texture2D>().Where(t=>t.name=="Synchronization QA fixture"))Object.Destroy(old);qaImage=null;
    }
    public static async Task<object> Run()
    {
        var v=Viewer;Require(v!=null&&v.column.Document!=null,"Viewer is not initialized");int first=v.AdvancingFrames;await Task.Delay(350);Require(v.AdvancingFrames>first,"Player loop frozen");
        Require(v.column.Document.SourceIntervalCount==131&&!v.DocumentSynchronized,"Supplied mismatched preview was incorrectly linked");
        Require(v.Root.Q<Label>("documentNotice").text.Contains("mismatch"),"Missing mismatch explanation");
        ShowFixture();await Task.Delay(200);Require(v.DocumentSynchronized,"Matching API fixture did not synchronize");
        var document=v.Root.Q("documentViewport");var viewport=v.Root.Q("viewport");var image=v.Root.Q<Image>("documentImage");var window=v.Root.Q("documentWindow");
        float width=image.resolvedStyle.width;Require(Math.Abs(window.resolvedStyle.width-width)<.1,"Document and overlay width differ");
        Require(Math.Abs(window.layout.center.y-document.contentRect.center.y)<.1,"Window is not vertically centered");
        float initialHeight=window.resolvedStyle.height;double initialDepth=v.FocusDepthFeet;float top=image.resolvedStyle.top;
        Wheel(viewport,3);await Task.Delay(100);Require(v.FocusDepthFeet>initialDepth&&image.resolvedStyle.top<top,"Cylinder wheel did not auto-scroll document");
        double depth=v.FocusDepthFeet;Wheel(document,2);await Task.Delay(100);Require(v.FocusDepthFeet>depth,"Document wheel did not move cylinder");
        depth=v.FocusDepthFeet;Drag(document,new Vector2(0,65));await Task.Delay(100);Require(v.FocusDepthFeet<depth,"Document drag did not move cylinder");
        float height=window.resolvedStyle.height;double span=v.WindowFeet;Submit(v.Root.Q<Button>("zoomIn"));await Task.Delay(100);Require(v.WindowFeet<span&&window.resolvedStyle.height<height,"Zoom did not shrink the window");Require(Math.Abs(image.resolvedStyle.width-width)<.1,"Zoom changed document width");
        var rotation=v.columnCamera.transform.rotation;Drag(viewport,new Vector2(40,15));await Task.Delay(100);Require(Quaternion.Angle(rotation,v.columnCamera.transform.rotation)>.1,"Cylinder orbit failed");
        Require(Math.Abs(window.layout.center.y-document.contentRect.center.y)<.1,"Orbit displaced centered document window");
        BoreholeImageMap.TryCreate(v.column.Document,out var map,out _);double expected=(map.ToPixel(v.VisibleBottomFeet)-map.ToPixel(v.VisibleTopFeet))*width/800;Require(Math.Abs(window.resolvedStyle.height-expected)<1.1,"Window bounds do not match visible cylinder depths");
        double previousWindow=v.WindowFeet;v.Zoom(.000001);await Task.Delay(120);expected=(map.ToPixel(v.VisibleBottomFeet)-map.ToPixel(v.VisibleTopFeet))*width/800;Require(Math.Abs(window.resolvedStyle.height-expected)<1.1,"Minimum zoom inflated the document window");
        var topPoint=v.column.transform.TransformPoint(new Vector3(0,v.column.DepthMap.ToY(v.VisibleTopFeet),0));var bottomPoint=v.column.transform.TransformPoint(new Vector3(0,v.column.DepthMap.ToY(v.VisibleBottomFeet),0));Require(Math.Abs(v.columnCamera.WorldToViewportPoint(topPoint).y-1)<.01&&Math.Abs(v.columnCamera.WorldToViewportPoint(bottomPoint).y)<.01,"Visible depth range disagrees with camera projection");
        v.Zoom(previousWindow/v.WindowFeet);await Task.Delay(100);
        v.SetDepth(1155);await Task.Delay(100);Require(v.column.Document.Intervals[v.column.SelectedIndex].IsGap,"No-data gap was not selected by linked navigation");
        v.SelectInterval(1,true);await Task.Delay(100);Physics.SyncTransforms();var row=v.column.Document.Intervals[1];var point=v.column.transform.TransformPoint(new Vector3(0,v.column.DepthMap.ToY((row.TopFeet+row.BottomFeet)*.5),0));Require(v.column.Pick(v.columnCamera.ViewportPointToRay(v.columnCamera.WorldToViewportPoint(point)))==1,"Cylinder pick failed after schematic width scaling");
        v.Root.Q<DropdownField>("appearance").value="Legend / patterns";Require(v.column.Surfaces.All(s=>s.displayMode==DisplayMode.Legend),"Legend mode failed");v.Root.Q<DropdownField>("appearance").value="Textured lithology";
        var previous=v.column.Document;Require(!v.LoadCsvText("top_depth,bottom_depth,lithology\n0,20,Shale\n10,30,Sandstone")&&ReferenceEquals(previous,v.column.Document)&&v.DocumentSynchronized,"Invalid import replaced valid linked result");
        var imagePicker=DebugCsvFileDialog.EditorImagePicker;bool pickerCalled=false;
        try{DebugCsvFileDialog.EditorImagePicker=()=>{pickerCalled=true;return Path.GetFullPath("Assets/Borehole/Samples/Allegheny-preview.png");};Submit(v.Root.Q<Button>("loadImage"));Require(pickerCalled&&v.DocumentPixelHeight==6736,"Image button did not invoke actual decoder");}
        finally{DebugCsvFileDialog.EditorImagePicker=imagePicker;}
        ShowFixture();await Task.Delay(100);
        var result=new{passed=true,firstFrame=first,finalFrame=v.AdvancingFrames,mismatchDetected=true,piecewiseMap=true,cylinderWheelScrollsDocument=true,documentWheelMovesCylinder=true,documentDragMovesCylinder=true,zoomChangesWindowHeightOnly=true,windowCentered=true,windowMatchesVisibleDepth=true,minimumZoomMatchesCamera=true,explicitOriginalDimensionsHonored=true,gapNavigation=true,orbitAndPicking=true,legendMode=true,invalidImportPreservesLinkedResult=true,imagePickerAndPngDecode=true};
        File.WriteAllText("Assets/Borehole/Generated/Verification/Sync/LiveResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result,Newtonsoft.Json.Formatting.Indented));return result;
    }
    public static object CheckCleanup()
    {
        int textures=Resources.FindObjectsOfTypeAll<RenderTexture>().Count(t=>t.name=="Borehole viewport"),meshes=Resources.FindObjectsOfTypeAll<Mesh>().Count(t=>t.name=="Shared capped borehole cylinder"),materials=Resources.FindObjectsOfTypeAll<Material>().Count(t=>t.name=="Borehole selection"),images=Resources.FindObjectsOfTypeAll<Texture2D>().Count(t=>t.name=="Borehole document image"||t.name=="Synchronization QA fixture");
        var result=new{playing=Application.isPlaying,textures,meshes,materials,images};Require(!Application.isPlaying&&textures==0&&meshes==0&&materials==0&&images==0,"Runtime resources leaked");File.WriteAllText("Assets/Borehole/Generated/Verification/Sync/CleanupResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result,Newtonsoft.Json.Formatting.Indented));return result;
    }
}
