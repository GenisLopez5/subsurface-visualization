using System;
using System.IO;
using System.Linq;
using NUnit.Framework;
using Subsurface.Lithology;
using Subsurface.Lithology.Editor;
using UnityEngine;

namespace Subsurface.Borehole.Tests
{
    public class BoreholeTests
    {
        [Test]public void SuppliedSampleUsesDepthsAndPreservesSeventeenIntervals()
        {
            var d=BoreholeCsv.Parse(File.ReadAllText("Assets/Borehole/Samples/example.csv"));Assert.That(d.SourceIntervalCount,Is.EqualTo(17));Assert.That(d.Intervals.Count,Is.EqualTo(17));Assert.That(d.TopFeet,Is.EqualTo(25400));Assert.That(d.BottomFeet,Is.EqualTo(25700));Assert.That(d.SpanFeet,Is.EqualTo(300));Assert.That(d.Intervals[0].Lithology,Is.EqualTo("Shale"));
        }
        [Test]public void ParserHandlesBomReorderedHeadersQuotesEscapesAndExtraColumns()
        {
            var d=BoreholeCsv.Parse("\uFEFFlithology,bottom_depth,top_y,top_depth\r\n\"Rock, \"\"unverified\"\"\",15,9999,10\r\n");Assert.That(d.TopFeet,Is.EqualTo(10));Assert.That(d.BottomFeet,Is.EqualTo(15));Assert.That(d.Intervals[0].Lithology,Is.EqualTo("Rock, \"unverified\""));
        }
        [Test]public void SortingAndGapsDoNotInventSurfaceToLogStartData()
        {
            var d=BoreholeCsv.Parse("top_depth,bottom_depth,lithology\n130,140,Sandstone\n100,110,Shale\n");Assert.That(d.Intervals.Count,Is.EqualTo(3));Assert.That(d.TopFeet,Is.EqualTo(100));var gap=d.Intervals[1];Assert.That(gap.IsGap,Is.True);Assert.That(gap.TopFeet,Is.EqualTo(110));Assert.That(gap.BottomFeet,Is.EqualTo(130));Assert.That(gap.Lithology,Is.EqualTo("No data"));
        }
        [TestCase("top_depth,bottom_depth,lithology\n0,5,Shale\n4,10,Sandstone")]
        [TestCase("top_depth,bottom_depth,lithology\n0,0,Shale")]
        [TestCase("top_depth,bottom_depth,lithology\n10,5,Shale")]
        [TestCase("top_depth,bottom_depth,lithology\n-1,5,Shale")]
        [TestCase("top_depth,bottom_depth,lithology\nNaN,5,Shale")]
        [TestCase("top_depth,bottom_depth,lithology\n0,Infinity,Shale")]
        [TestCase("top_depth,bottom_depth,lithology\n0,5")]
        [TestCase("top_depth,bottom_depth,lithology\n0,5,\"unfinished")]
        [TestCase("top_depth,bottom_depth,lithology\n0,5,\"rock\"trailing")]
        [TestCase("top_depth,top_depth,bottom_depth,lithology\n0,0,5,Shale")]
        [TestCase("top_depth,bottom_depth,rock\n0,5,Shale")]
        public void InvalidInputCannotBecomeAnApparentlyValidColumn(string csv)=>Assert.Throws<FormatException>(()=>BoreholeCsv.Parse(csv));
        [Test]public void AdjacentEqualLabelsRemainDistinctAndUnknownTextIsPreserved()
        {
            var d=BoreholeCsv.Parse("top_depth,bottom_depth,lithology\n0,5,Shale\n5,10,Shale\n10,15,unmapped-text\n");Assert.That(d.Intervals.Count,Is.EqualTo(3));Assert.That(d.Intervals[2].Lithology,Is.EqualTo("unmapped-text"));Assert.That(d.Intervals.All(i=>!i.IsGap),Is.True);
        }
        [Test]public void SchematicMapHasBoundedHeightAndAccurateFeet()
        {
            var map=new BoreholeDepthMap(25400,25700,24);Assert.That(map.ToY(25400),Is.Zero);Assert.That(map.ToY(25700),Is.EqualTo(-24));Assert.That(map.ToY(25410),Is.EqualTo(-.8f).Within(.00001f));Assert.That(map.ToFeet(map.ToY(25630)),Is.EqualTo(25630).Within(.001));
        }
        [Test]public void MeshHasActualCapsAndOutwardNormals()
        {
            var mesh=BoreholeColumn.CreateCylinder(32);
            try{Assert.That(mesh.bounds.min.y,Is.EqualTo(-1).Within(.00001));Assert.That(mesh.bounds.max.y,Is.Zero.Within(.00001));Assert.That(mesh.normals.Count(n=>n==Vector3.up),Is.GreaterThan(30));Assert.That(mesh.normals.Count(n=>n==Vector3.down),Is.GreaterThan(30));var v=mesh.vertices;var normals=mesh.normals;var t=mesh.triangles;for(int i=0;i<t.Length;i+=3){var face=Vector3.Cross(v[t[i+1]]-v[t[i]],v[t[i+2]]-v[t[i]]).normalized;Assert.That(Vector3.Dot(face,normals[t[i]]),Is.GreaterThan(.95f));}}
            finally{UnityEngine.Object.DestroyImmediate(mesh);}
        }
        [Test]public void ColumnSharesMeshesAppliesCatalogueAndRebuildsWithoutDuplicates()
        {
            var go=new GameObject("Borehole test");
            try
            {
                var column=go.AddComponent<BoreholeColumn>();column.catalog=LithologyBuilder.Catalog;
                var data=BoreholeCsv.Parse("top_depth,bottom_depth,lithology\n100,110,Shale\n120,130,Sandstone");column.Build(data);
                Assert.That(column.Surfaces.Count,Is.EqualTo(3));var one=column.Surfaces[0].GetComponent<MeshFilter>().sharedMesh;Assert.That(column.Surfaces.All(s=>s.GetComponent<MeshFilter>().sharedMesh==one),Is.True);
                Assert.That(column.Surfaces[1].rawLabel,Is.EqualTo("Not Available"));Assert.That(column.Surfaces[0].target.sharedMaterial.shader,Is.EqualTo(column.catalog.shader));column.Select(1);Assert.That(column.SelectedIndex,Is.EqualTo(1));
                column.Build(data);Assert.That(column.transform.childCount,Is.EqualTo(1));Assert.That(column.Surfaces.Count,Is.EqualTo(3));
                column.ReleaseRuntimeResources(true);Assert.That(column.transform.childCount,Is.Zero);Assert.That(column.Surfaces,Is.Empty);Assert.That(column.Document,Is.Null);Assert.That(one==null,Is.True);
            }finally{UnityEngine.Object.DestroyImmediate(go);}
        }
    }
}
