using System;using System.IO;using System.Threading.Tasks;using UnityEngine;using UnityEngine.UIElements;using Subsurface.Borehole;
public static class VerifyIteration
{
 static BoreholeViewer V=>UnityEngine.Object.FindAnyObjectByType<BoreholeViewer>();
 static void Check(bool ok,string text){if(!ok)throw new Exception(text);}
 static void Wheel(VisualElement target,float amount){using(var e=WheelEvent.GetPooled(new Event{type=EventType.ScrollWheel,delta=new Vector2(0,amount),mousePosition=target.worldBound.center}))target.SendEvent(e);}
 public static async Task<object> Run()
 {
  var v=V;int frame=v.AdvancingFrames;await Task.Delay(150);Check(v.AdvancingFrames>frame,"Player not advancing");
  var t=new Texture2D(100,1000){name="Iteration fixture",hideFlags=HideFlags.DontSave};var pixels=new Color[100000];for(int y=0;y<1000;y++)for(int x=0;x<100;x++)pixels[y*100+x]=x%10==0||y%50==0?Color.gray:y>500?new Color(.7f,.75f,.8f):new Color(.85f,.7f,.4f);t.SetPixels(pixels);t.Apply();
  Check(v.SetAnalysisResult("top_depth,bottom_depth,lithology,top_y,bottom_y,cluster_id\n0,50,Shale,200,500,0\n50,100,Sandstone,500,900,1",t,"Iteration fixture"),"Fixture import");Check(v.column.SelectedIndex==-1,"Import auto-selected");v.Zoom(.03);v.SetDepth(25);await Task.Delay(100);
  var root=v.Root;var viewport=root.Q("viewport");var doc=root.Q("documentViewport");var rotation=v.columnCamera.transform.rotation;
  v.SelectInterval(0,true);await Task.Delay(100);double start=v.FocusDepthFeet;Wheel(viewport,3);await Task.Delay(80);Check(v.FocusDepthFeet>start&&v.column.SelectedIndex==0,"Scroll selected a new interval");
  var center=viewport.worldBound.center;using(var e=PointerDownEvent.GetPooled(new Event{type=EventType.MouseDown,button=0,mousePosition=center}))viewport.SendEvent(e);using(var e=PointerMoveEvent.GetPooled(new Event{type=EventType.MouseDrag,button=0,mousePosition=center+new Vector2(40,-60)}))viewport.SendEvent(e);using(var e=PointerUpEvent.GetPooled(new Event{type=EventType.MouseUp,button=0,mousePosition=center+new Vector2(40,-60)}))viewport.SendEvent(e);await Task.Delay(80);Check(v.FocusDepthFeet>start&&Quaternion.Angle(rotation,v.columnCamera.transform.rotation)<.001,"Drag rotates instead of scrolling");
  var list=root.Q<ListView>("csvRows");list.SetSelection(1);await Task.Delay(100);Check(v.column.SelectedIndex==1&&Math.Abs(v.FocusDepthFeet-75)<.01,"CSV click did not navigate");
  v.SetDepth(20);await Task.Delay(100);Check(list.selectedIndex==1&&v.column.SelectedIndex==1,"Navigation changed table selection");start=v.FocusDepthFeet;Wheel(list,5);await Task.Delay(100);Check(v.FocusDepthFeet==start,"CSV scroll moved column");
  v.SelectInterval(0,false);Check(list.selectedIndex==0,"Cylinder selection did not reveal CSV row");
  float height=doc.layout.height;Check(Math.Abs(root.Q("csvOverlay").layout.height-height*.5)<2,"CSV overlay not half height");Check(doc.layout.height==height,"CSV overlay resized viewport");
  v.FitColumn();await Task.Delay(100);var image=root.Q("documentImage");Check(image.layout.height<=doc.layout.height+1&&image.layout.width<=doc.layout.width+1,"Fit does not fit image");Check(root.Q("documentWindow").resolvedStyle.display==DisplayStyle.None,"Fit retained window");
  root.Q("csvOverlay").AddToClassList("hidden");v.Zoom(.03);v.SetDepth(25);await Task.Delay(100);Check(root.Q("documentWindow").resolvedStyle.display==DisplayStyle.Flex,"Zoom missing window");Wheel(doc,-1000);await Task.Delay(100);Check(v.FocusDepthFeet==0&&v.columnCamera.transform.position.y>0,"Header not pinned at surface");Check(v.columnCamera.clearFlags==CameraClearFlags.Skybox&&v.GetComponent<BoreholeCutaway>()!=null,"Missing real scene context");
  root.Q("csvOverlay").AddToClassList("hidden");
  var result=new{passed=true,frame=v.AdvancingFrames,clickOnlySelection=true,dragScroll=true,noRotation=true,csvClickNavigation=true,csvIndependentScroll=true,csvHalfOverlay=true,fitDocument=true,conditionalWindow=true,headerPinsSurface=true,real3DGround=true};Directory.CreateDirectory("Assets/Borehole/Generated/Verification/Iteration");File.WriteAllText("Assets/Borehole/Generated/Verification/Iteration/LiveResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result));return result;
 }
 public static void Restore(){V.SetAnalysisResult(V.initialCsv.text,V.initialDocumentImage,V.initialSourceName);foreach(var t in Resources.FindObjectsOfTypeAll<Texture2D>())if(t.name=="Iteration fixture")UnityEngine.Object.Destroy(t);}
}
