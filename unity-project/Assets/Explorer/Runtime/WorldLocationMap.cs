using System;
using UnityEngine;
using UnityEngine.UIElements;

namespace Subsurface.Explorer
{
    /// <summary>A lightweight, offline world overview for document locations.</summary>
    public sealed class WorldLocationMap : VisualElement
    {
        static readonly Vector2[][] Land=
        {
            Points(-168,72,-140,70,-124,55,-100,50,-82,25,-98,18,-120,31,-132,51,-168,60),
            Points(-82,12,-68,7,-50,-10,-38,-23,-54,-55,-70,-48,-78,-15),
            Points(-72,82,-20,80,-25,61,-55,59),
            Points(-18,71,40,70,70,55,110,60,150,52,178,65,170,35,135,20,110,5,80,8,55,28,40,10,20,35,10,45,-10,36,-25,55),
            Points(-17,35,12,37,40,20,51,10,38,-35,18,-35,-5,-5),
            Points(112,-10,155,-12,151,-40,116,-35),
            Points(-180,-72,-125,-68,-65,-73,0,-69,60,-73,125,-67,180,-72,180,-90,-180,-90),
        };

        bool hasLocation;
        double latitude,longitude;

        public WorldLocationMap()
        {
            AddToClassList("world-map");
            pickingMode=PickingMode.Ignore;
            generateVisualContent+=Draw;
        }

        public void SetLocation(double newLatitude,double newLongitude)
        {
            if(!Valid(newLatitude,newLongitude))throw new ArgumentOutOfRangeException(nameof(newLatitude),"Coordinates must be finite latitude/longitude values.");
            latitude=newLatitude;longitude=newLongitude;hasLocation=true;MarkDirtyRepaint();
        }

        public void ClearLocation(){hasLocation=false;MarkDirtyRepaint();}

        public static bool Valid(double candidateLatitude,double candidateLongitude)
            =>!double.IsNaN(candidateLatitude)&&!double.IsInfinity(candidateLatitude)&&candidateLatitude>=-90&&candidateLatitude<=90
              &&!double.IsNaN(candidateLongitude)&&!double.IsInfinity(candidateLongitude)&&candidateLongitude>=-180&&candidateLongitude<=180;

        public static Vector2 Project(double candidateLatitude,double candidateLongitude,Rect bounds)
        {
            float x=bounds.x+(float)((candidateLongitude+180)/360)*bounds.width;
            float y=bounds.y+(float)((90-candidateLatitude)/180)*bounds.height;
            return new Vector2(x,y);
        }

        void Draw(MeshGenerationContext context)
        {
            var bounds=new Rect(10,10,Mathf.Max(0,contentRect.width-20),Mathf.Max(0,contentRect.height-20));
            if(bounds.width<2||bounds.height<2)return;
            var painter=context.painter2D;

            painter.lineWidth=1;painter.strokeColor=new Color(.31f,.22f,.40f,.11f);
            for(int longitudeLine=-120;longitudeLine<=120;longitudeLine+=60)Line(painter,Project(-90,longitudeLine,bounds),Project(90,longitudeLine,bounds));
            for(int latitudeLine=-60;latitudeLine<=60;latitudeLine+=30)Line(painter,Project(latitudeLine,-180,bounds),Project(latitudeLine,180,bounds));

            painter.fillColor=new Color(.32f,.23f,.42f,.20f);painter.strokeColor=new Color(.31f,.22f,.40f,.30f);painter.lineWidth=1;
            foreach(var polygon in Land)Polygon(painter,polygon,bounds);
            if(!hasLocation)return;

            var point=Project(latitude,longitude,bounds);
            painter.fillColor=new Color(.47f,1f,0f,.18f);Circle(painter,point,11,20);
            painter.fillColor=new Color(.47f,1f,0f,1f);Circle(painter,point,6,16);
            painter.fillColor=new Color(.33f,0f,.71f,1f);Circle(painter,point,2.5f,12);
        }

        static void Line(Painter2D painter,Vector2 from,Vector2 to)
        {painter.BeginPath();painter.MoveTo(from);painter.LineTo(to);painter.Stroke();}

        static void Polygon(Painter2D painter,Vector2[] coordinates,Rect bounds)
        {
            painter.BeginPath();painter.MoveTo(Project(coordinates[0].y,coordinates[0].x,bounds));
            for(int i=1;i<coordinates.Length;i++)painter.LineTo(Project(coordinates[i].y,coordinates[i].x,bounds));
            painter.ClosePath();painter.Fill();painter.Stroke();
        }

        static void Circle(Painter2D painter,Vector2 center,float radius,int segments)
        {
            painter.BeginPath();
            for(int i=0;i<segments;i++)
            {
                float angle=i*Mathf.PI*2/segments;var point=center+new Vector2(Mathf.Cos(angle),Mathf.Sin(angle))*radius;
                if(i==0)painter.MoveTo(point);else painter.LineTo(point);
            }
            painter.ClosePath();painter.Fill();
        }

        static Vector2[] Points(params float[] longitudeLatitude)
        {
            var result=new Vector2[longitudeLatitude.Length/2];
            for(int i=0;i<result.Length;i++)result[i]=new Vector2(longitudeLatitude[i*2],longitudeLatitude[i*2+1]);
            return result;
        }
    }
}
