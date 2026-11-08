using System;using System.IO;using System.Linq;using System.Threading.Tasks;using UnityEngine;using UnityEngine.UIElements;using Subsurface.Borehole;
public static class VerifyStableTags
{
 static BoreholeViewer V=>UnityEngine.Object.FindAnyObjectByType<BoreholeViewer>();static void Check(bool ok,string text){if(!ok)throw new Exception(text);}
 static Button Tag(int i)=>V.Root.Query<Button>(className:"interval-label").ToList().FirstOrDefault(l=>l.resolvedStyle.display==DisplayStyle.Flex&&l.userData is int index&&index==i);
 static float Project(double d){var v=V;return v.Root.Q("viewport").contentRect.height*(1-v.columnCamera.WorldToViewportPoint(v.column.transform.TransformPoint(new Vector3(0,v.column.DepthMap.ToY(d),0))).y);}
 public static async Task<object> Run()
 {
  var v=V;int frame=v.AdvancingFrames;await Task.Delay(100);Check(v.AdvancingFrames>frame,"Frozen player");
  v.LoadCsvText("top_depth,bottom_depth,lithology\n1000,1400,Shale\n1400,1450,Sandstone\n1450,1500,Limestone\n1500,1550,Shale\n1550,1600,Sandstone\n1600,1650,Limestone\n1650,1700,Shale","Stable tag fixture");v.Zoom(.5);v.SetDepth(1170);await Task.Delay(100);
  var first=Tag(0);Check(first!=null&&Math.Abs(first.style.top.value.value+21-Project(1200))<.1,"Unselected tag not at true midpoint");float before=first.style.top.value.value;
  v.SetDepth(1190);await Task.Delay(100);Check(Tag(0)!=null&&Math.Abs(Tag(0).style.top.value.value+21-Project(1200))<.1&&Math.Abs(Tag(0).style.top.value.value-before)>30,"Unselected tag follows camera");
  v.SetDepth(1500);await Task.Delay(100);var third=Tag(3);Check(third!=null,"Expected side-test tag missing");float x=third.style.left.value.value;v.SetDepth(1530);await Task.Delay(100);Check(Tag(3)!=null&&Math.Abs(Tag(3).style.left.value.value-x)<.1&&x>v.Root.Q("viewport").contentRect.width*.5,"Tag changed side when earlier tag left view");
  v.Zoom(.4);v.SetDepth(1100);await Task.Delay(100);Check(Tag(0)==null,"Unselected midpoint outside view did not leave view");
  v.SelectInterval(0,false);await Task.Delay(100);float y=Tag(0).style.top.value.value;v.SetDepth(1120);await Task.Delay(100);Check(Tag(0)!=null&&Math.Abs(Tag(0).style.top.value.value-y)<1,"Selected tag does not follow camera");
  v.SetDepth(1600);await Task.Delay(100);Check(Tag(0)!=null&&Tag(0).text.Contains("↑"),"Selected offscreen tag disappeared or lost direction");Check(Tag(0).style.top.value.value>=49,"Selected tag is outside viewport");
  v.SelectInterval(5,false);await Task.Delay(100);Check(Tag(0)==null,"Deselected tag kept following camera");Check(v.Root.Q("csvOverlay").ClassListContains("hidden"),"Tag selection opened CSV");
  v.FitColumn();await Task.Delay(100);Check(Tag(5)!=null,"Selected tag hidden at overview");
  v.SetDepth(1530);v.Zoom(.16);await Task.Delay(100);
  var result=new{passed=true,frames=v.AdvancingFrames-frame,unselectedTrueMidpoints=true,stableSectionSides=true,selectedFollowsCamera=true,offscreenSelectedRetained=true,deselectionRestoresFixedAnchor=true,selectedSurvivesOverview=true,csvStaysClosed=true};Directory.CreateDirectory("Assets/Borehole/Generated/Verification/StableTags");File.WriteAllText("Assets/Borehole/Generated/Verification/StableTags/LiveResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result));return result;
 }
 public static void Restore(){V.SetAnalysisResult(V.initialCsv.text,V.initialDocumentImage,V.initialSourceName);}
}
