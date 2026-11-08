using System;using System.Linq;using System.IO;using System.Threading.Tasks;using UnityEngine;using UnityEngine.UIElements;using Subsurface.Borehole;
public static class VerifyFinalVisuals
{
 static BoreholeViewer V=>UnityEngine.Object.FindAnyObjectByType<BoreholeViewer>();static void Check(bool ok,string why){if(!ok)throw new Exception(why);}
 static string Csv(){string s="top_depth,bottom_depth,lithology,top_y,bottom_y\n";for(int i=0;i<8;i++)s+=(1000+i*10)+","+(1010+i*10)+","+(i%2==0?"Shale":"Sandstone")+","+(100+i*100)+","+(200+i*100)+"\n";return s;}
 public static async Task<object> Run()
 {
  var v=V;int frame=v.AdvancingFrames;await Task.Delay(150);Check(v.AdvancingFrames>frame,"Player frozen");
  var texture=new Texture2D(100,1000){name="Final visuals fixture",hideFlags=HideFlags.DontSave};var pixels=new Color[100000];for(int y=0;y<1000;y++)for(int x=0;x<100;x++)pixels[y*100+x]=x%20==0||y%100==0?Color.gray:new Color(.9f,.88f,.82f);texture.SetPixels(pixels);texture.Apply();
  v.SetAnalysisResult(Csv(),texture,"Selection/connector fixture");v.Zoom(.3);v.SetDepth(1035);await Task.Delay(150);
  var root=v.Root;var viewport=root.Q("viewport");var doc=root.Q("documentViewport");var image=root.Q("documentImage");var band=root.Q("documentSelection");
  Check(band.resolvedStyle.display==DisplayStyle.None,"Unexpected selection on import");
  float ratio=viewport.layout.width/(viewport.layout.width+doc.layout.width);Check(Math.Abs(ratio-.372)<.01,"Viewport not approximately 20% smaller");
  var axis=v.columnCamera.WorldToViewportPoint(v.column.transform.TransformPoint(new Vector3(0,v.column.DepthMap.ToY(v.FocusDepthFeet),0)));Check(Math.Abs(axis.x-.5)<.001,"Column not centered");
  var soil=v.GetComponentsInChildren<Renderer>().First(r=>r.name=="Exposed ground interior");Check(soil.bounds.IntersectRay(v.columnCamera.ViewportPointToRay(new Vector3(.99f,.5f,0))),"Right side ray misses ground");
  var labels=root.Query<Button>(className:"interval-label").ToList().Where(l=>l.resolvedStyle.display==DisplayStyle.Flex).ToArray();var lines=root.Query<VisualElement>(className:"interval-connector").ToList().Where(l=>l.resolvedStyle.display==DisplayStyle.Flex).ToArray();Check(labels.Length>=2&&lines.Length==labels.Length,"Missing connectors");for(int i=0;i<labels.Length;i++)Check(Math.Abs(lines[i].layout.center.y-labels[i].layout.center.y)<1.1&&lines[i].layout.width>0,"Connector not aligned");
  int target=3;Physics.SyncTransforms();var click=viewport.worldBound.center;
  using(var e=PointerDownEvent.GetPooled(new Event{type=EventType.MouseDown,button=0,mousePosition=click}))viewport.SendEvent(e);
  using(var e=PointerUpEvent.GetPooled(new Event{type=EventType.MouseUp,button=0,mousePosition=click}))viewport.SendEvent(e);
  Check(v.column.SelectedIndex==target,"Cylinder pointer click selected the wrong interval");await Task.Delay(150);Check(root.Q("csvOverlay").ClassListContains("hidden"),"Selection opened CSV");Check(band.resolvedStyle.display==DisplayStyle.Flex,"No selected document band");
  float scale=image.style.width.value.value/100;Check(Math.Abs(band.layout.y-(image.style.top.value.value+400*scale))<1.1&&Math.Abs(band.layout.height-100*scale)<1.1,"Selection band does not match source pixels");
  v.Zoom(.7);await Task.Delay(120);scale=image.style.width.value.value/100;Check(Math.Abs(band.layout.y-(image.style.top.value.value+400*scale))<1.1,"Zoom lost pixel alignment");
  v.SetDepth(1039);await Task.Delay(100);Check(v.column.SelectedIndex==target,"Scroll changed selection");
  v.FitColumn();await Task.Delay(100);Check(root.Q("documentWindow").resolvedStyle.display==DisplayStyle.None&&band.resolvedStyle.display==DisplayStyle.Flex,"Fit lost selection or retained navigation window");
  v.LoadCsvText("top_depth,bottom_depth,lithology,top_y,bottom_y\n1000,1010,Shale,100,200\n1020,1030,Sandstone,300,400","Gap fixture");v.SetAnalysisResult("top_depth,bottom_depth,lithology,top_y,bottom_y\n1000,1010,Shale,100,200\n1020,1030,Sandstone,300,400",texture,"Gap fixture");v.SelectInterval(1,false);await Task.Delay(100);Check(band.resolvedStyle.display==DisplayStyle.None,"Gap fabricated a document band");
  v.SetAnalysisResult(Csv(),texture,"Selection/connector fixture");v.Zoom(.3);v.SetDepth(1035);v.SelectInterval(3,false);await Task.Delay(100);
  var result=new{passed=true,frames=v.AdvancingFrames-frame,smallerPane=true,columnCentered=true,rightEdgeGround=true,connectorsAligned=true,exactPixelSelection=true,zoomTracksSelection=true,fitRetainsSelection=true,csvStaysClosed=true,gapDoesNotInventPixels=true};Directory.CreateDirectory("Assets/Borehole/Generated/Verification/FinalVisuals");File.WriteAllText("Assets/Borehole/Generated/Verification/FinalVisuals/LiveResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result));return result;
 }
 public static async Task<object> Restore()
 {V.SetAnalysisResult(V.initialCsv.text,V.initialDocumentImage,V.initialSourceName);await Task.Delay(100);Check(V.Root.Q("documentSelection").resolvedStyle.display==DisplayStyle.None,"Stale band on mismatched document");foreach(var t in Resources.FindObjectsOfTypeAll<Texture2D>())if(t.name=="Final visuals fixture")UnityEngine.Object.Destroy(t);return "Provided files restored; incompatible image does not receive a false highlight";}
}
